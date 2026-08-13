"""Ghost Call Monitor - Detects and rescues stuck calls"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .database import SessionLocal
from .models import Call, CallStatus
from .threecx import ThreeCXClient

logger = logging.getLogger(__name__)


def check_3cx_call_status(call: Call):
    settings = get_settings()
    if settings.call_provider != "threecx":
        return None
    
    try:
        client = ThreeCXClient(settings)
        try:
            response = client.client.get(
                f"/callcontrol/{settings.threecx_app_id}",
                headers=client._authorized_headers()
            )
            
            if response.status_code == 200:
                data = response.json()
                participants = data.get("participants", [])
                
                if call.provider_call_id:
                    for p in participants:
                        if str(p.get("id")) == str(call.provider_call_id):
                            return True
                
                return False
            
            return None
        finally:
            client.close()
            
    except Exception as e:
        logger.error(f"Error checking 3CX status for call {call.id}: {e}")
        return None


def monitor_and_rescue_ghost_calls():
    db = SessionLocal()
    
    try:
        threshold = datetime.now(timezone.utc) - timedelta(minutes=15)
        potentially_stuck = db.query(Call).filter(
            Call.status == CallStatus.in_progress,
            Call.started_at < threshold
        ).all()
        
        if not potentially_stuck:
            return
        
        logger.info(f"Checking {len(potentially_stuck)} potentially stuck calls")
        
        for call in potentially_stuck:
            age_minutes = (datetime.now(timezone.utc) - call.started_at).total_seconds() / 60
            
            is_active = check_3cx_call_status(call)
            
            if is_active == False:
                logger.warning(f"Ghost call rescued: {call.id} age={age_minutes:.1f}min")
                call.status = CallStatus.failed
                call.failure_reason = "Ghost call - 3CX confirmed ended"
                call.completed_at = datetime.now(timezone.utc)
                db.commit()
                
            elif is_active == True:
                logger.info(f"Long call (legit): {call.id} age={age_minutes:.1f}min")
            else:
                logger.error(f"Cannot verify call {call.id}")
    
    except Exception as e:
        logger.error(f"Ghost monitor error: {e}", exc_info=True)
    finally:
        db.close()


def ghost_monitor_loop():
    logger.info("Ghost call monitor started")
    
    while True:
        try:
            monitor_and_rescue_ghost_calls()
        except Exception as e:
            logger.error(f"Ghost monitor loop error: {e}", exc_info=True)
        
        time.sleep(300)


class GhostCallMonitor:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
    
    def start(self):
        self.thread = threading.Thread(
            target=ghost_monitor_loop,
            name="ghost-monitor",
            daemon=True
        )
        self.thread.start()
        logger.info("Ghost call monitor thread started")
    
    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Ghost call monitor stopped")
