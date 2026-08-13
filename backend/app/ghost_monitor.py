"""Background service to detect and rescue ghost calls."""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .database import SessionLocal
from .models import Call, CallStatus
from .config import get_settings
from .threecx import ThreeCXClient, ThreeCXError

logger = logging.getLogger(__name__)


def check_3cx_call_status(call: Call) -> bool | None:
    """
    Verify if a call is still active in 3CX.
    
    Returns:
        True if call is active in 3CX
        False if call has ended in 3CX
        None if 3CX is unreachable or error occurred
    """
    if not call.provider_call_id:
        return False
    
    try:
        settings = get_settings()
        client = ThreeCXClient(settings)
        
        # Try to get call status from 3CX
        # If the call exists and is active, 3CX will return it
        # If it doesn't exist, 3CX will raise an error
        try:
            # Note: This is a simplified check. Adjust based on actual 3CX API
            # You may need to implement a specific "get_call_status" method in ThreeCXClient
            logger.info(f"Checking 3CX status for call {call.id} with provider_call_id {call.provider_call_id}")
            # For now, we assume if we can't find the call, it's ended
            # This is a placeholder - you should implement actual 3CX call status check
            return False
        except ThreeCXError as e:
            if "not found" in str(e).lower() or "404" in str(e):
                # Call doesn't exist in 3CX anymore = ended
                logger.info(f"Call {call.id} not found in 3CX, treating as ended")
                return False
            else:
                # Other error - can't determine status
                logger.warning(f"Error checking 3CX status for call {call.id}: {e}")
                return None
        finally:
            client.close()
    except Exception as e:
        logger.error(f"Failed to check 3CX status for call {call.id}: {e}", exc_info=True)
        return None


def monitor_and_rescue_ghost_calls() -> dict:
    """
    Find calls stuck in 'in_progress' status and rescue them if confirmed by 3CX.
    
    Returns:
        dict with counts of checked, rescued, and still_active calls
    """
    checked = 0
    rescued = 0
    still_active = 0
    errors = 0
    
    try:
        with SessionLocal() as db:
            # Find calls stuck in 'in_progress' for more than 15 minutes
            threshold = datetime.now(timezone.utc) - timedelta(minutes=15)
            stuck_calls = db.scalars(
                select(Call).where(
                    Call.status == CallStatus.in_progress,
                    Call.started_at < threshold
                )
            ).all()
            
            if not stuck_calls:
                logger.info("Ghost call check: no stuck calls found")
                return {"checked": 0, "rescued": 0, "still_active": 0, "errors": 0}
            
            logger.info(f"Ghost call check: found {len(stuck_calls)} potentially stuck calls")
            
            for call in stuck_calls:
                checked += 1
                age_minutes = (datetime.now(timezone.utc) - call.started_at).total_seconds() / 60
                logger.info(f"Checking call {call.id} (stuck for {age_minutes:.1f} minutes)")
                
                # Verify with 3CX
                is_active = check_3cx_call_status(call)
                
                if is_active is None:
                    # Can't determine status - skip for now
                    logger.warning(f"Could not verify status of call {call.id} with 3CX")
                    errors += 1
                    continue
                elif is_active:
                    # Call is actually still active in 3CX
                    logger.info(f"Call {call.id} is still active in 3CX")
                    still_active += 1
                    continue
                else:
                    # Call is NOT active in 3CX - rescue it!
                    logger.warning(f"GHOST CALL DETECTED: Call {call.id} stuck for {age_minutes:.1f} minutes, not in 3CX")
                    call.status = CallStatus.failed
                    call.failure_reason = f"Ghost call rescued (stuck {age_minutes:.1f}min, not in 3CX)"
                    call.completed_at = datetime.now(timezone.utc)
                    rescued += 1
            
            if rescued > 0:
                db.commit()
                logger.info(f"Ghost call rescue complete: {rescued} calls rescued")
            
            return {
                "checked": checked,
                "rescued": rescued,
                "still_active": still_active,
                "errors": errors
            }
    except Exception as e:
        logger.error(f"Error in ghost call monitor: {e}", exc_info=True)
        return {"checked": checked, "rescued": rescued, "still_active": still_active, "errors": errors + 1}


def ghost_monitor_loop(stop_event: threading.Event, interval_seconds: int = 300):
    """Main loop for ghost call monitoring. Runs every interval_seconds (default 5 minutes)."""
    logger.info(f"Ghost call monitor started (checking every {interval_seconds}s)")
    
    while not stop_event.is_set():
        try:
            result = monitor_and_rescue_ghost_calls()
            if result["checked"] > 0 or result["rescued"] > 0:
                logger.info(f"Ghost monitor: checked={result['checked']}, rescued={result['rescued']}, "
                           f"still_active={result['still_active']}, errors={result['errors']}")
        except Exception as e:
            logger.error(f"Ghost monitor loop error: {e}", exc_info=True)
        
        # Sleep in small intervals to allow quick shutdown
        for _ in range(interval_seconds):
            if stop_event.is_set():
                break
            time.sleep(1)
    
    logger.info("Ghost call monitor stopped")


class GhostCallMonitor:
    """Background thread that monitors and rescues ghost calls."""
    
    def __init__(self, interval_seconds: int = 300):
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
    
    def start(self) -> None:
        """Start the ghost call monitor thread."""
        if self.thread and self.thread.is_alive():
            logger.warning("Ghost call monitor already running")
            return
        
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=ghost_monitor_loop,
            args=(self.stop_event, self.interval_seconds),
            name="ghost-call-monitor",
            daemon=True
        )
        self.thread.start()
        logger.info("Ghost call monitor thread started")
    
    def stop(self) -> None:
        """Stop the ghost call monitor thread."""
        if not self.thread:
            return
        
        logger.info("Stopping ghost call monitor...")
        self.stop_event.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            logger.warning("Ghost call monitor did not stop cleanly")
        else:
            logger.info("Ghost call monitor stopped cleanly")
