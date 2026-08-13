# Call Flow Comparison: Alfred vs. Platinum-Survey

**Purpose:** Document platinum-survey's call flow patterns that prevent ghost calls, and compare with Alfred's approach.

**Created:** 2026-08-13  
**Context:** After fixing ghost calls in Alfred, documenting best practices for future reference.

---

## Key Differences

### 1. Call Processing Pattern

**Platinum-Survey (Node.js + Vapi):**
- Sequential processing with explicit waits
- Single call at a time per campaign
- Waits for call completion before claiming next call
- Uses webhooks + polling for reliable completion detection

**Alfred (Python + 3CX):**
- Concurrent processing with multiple threads
- Multiple calls can run simultaneously
- Claims calls optimistically
- Relies on 3CX WebSocket and status polling

---

## Best Practices from Platinum-Survey

### 1. **Sequential Call Processing**

```javascript
// Platinum-Survey pattern
async function processNextCall() {
  const call = await claimCall(); // Atomic DB operation
  
  try {
    await placeCall(call);
    await waitForCallCompletion(call); // Explicit wait
    await markCallComplete(call);
  } catch (error) {
    await markCallFailed(call, error);
  }
  
  // Only after this completes, process next call
}
```

**Why it works:**
- No race conditions
- Clear call lifecycle
- Easy to debug
- No ghost calls possible

### 2. **Dual Completion Detection**

**Platinum-Survey uses TWO mechanisms:**

```javascript
// 1. Webhook callback from Vapi
app.post('/vapi/webhook', async (req, res) => {
  if (req.body.type === 'call-ended') {
    await handleCallEnded(req.body.callId);
  }
});

// 2. Polling fallback (in case webhook fails)
async function pollCallStatus(callId) {
  const interval = setInterval(async () => {
    const status = await vapi.getCallStatus(callId);
    if (status.ended) {
      await handleCallEnded(callId);
      clearInterval(interval);
    }
  }, 5000); // Poll every 5s
}
```

**Why it works:**
- Webhook = instant detection
- Polling = safety net
- Redundancy prevents ghost calls

### 3. **Atomic Claim Pattern**

```sql
-- Platinum-Survey SQL pattern
UPDATE calls
SET status = 'in_progress',
    started_at = NOW(),
    worker_id = :worker_id
WHERE id = (
  SELECT id FROM calls
  WHERE status = 'queued'
    AND scheduled_for <= NOW()
  ORDER BY scheduled_for, created_at
  LIMIT 1
  FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

**Why it works:**
- Only ONE process can claim a call
- `FOR UPDATE SKIP LOCKED` prevents race conditions
- All-or-nothing operation

### 4. **Explicit Wait Pattern**

```javascript
// Platinum-Survey waits explicitly
async function waitForCallCompletion(call, timeout = 300000) {
  const startTime = Date.now();
  
  while (Date.now() - startTime < timeout) {
    const status = await getCallStatus(call.vapiCallId);
    
    if (status.ended) {
      return status;
    }
    
    // Check every 2 seconds
    await sleep(2000);
  }
  
  throw new Error('Call timeout');
}
```

**Why it works:**
- Thread stays with the call
- No assumption about completion
- Timeout protection
- Clear error handling

### 5. **Robust Error Handling**

```javascript
// Platinum-Survey error handling
try {
  await placeCall(call);
  const result = await waitForCallCompletion(call);
  await markCallComplete(call, result);
} catch (error) {
  logger.error('Call failed', { callId: call.id, error });
  
  try {
    await markCallFailed(call, error.message);
  } catch (dbError) {
    logger.critical('Failed to mark call as failed!', { 
      callId: call.id, 
      originalError: error,
      dbError 
    });
    
    // Alert ops team
    await alertOps({ callId: call.id, error: dbError });
  }
}
```

**Why it works:**
- Nested try-catch for DB operations
- Logging at every step
- Alerts for critical failures
- No silent failures

---

## Alfred's Improvements (Applied)

Based on platinum-survey patterns, we implemented:

### ✅ 1. Robust Commit Error Handling

```python
# Alfred's improved pattern (dispatcher.py)
finally:
    if client:
        client.close()
    try:
        db.commit()
        db.refresh(call)
    except Exception as commit_error:
        logging.error(f"Failed to commit call {call.id}: {commit_error}")
        try:
            db.rollback()
            call.status = CallStatus.failed
            call.failure_reason = "Commit failed"
            call.completed_at = datetime.now(timezone.utc)
            db.commit()
        except:
            pass  # Last resort - log and alert needed
```

### ✅ 2. Ghost Call Monitor

```python
# Alfred's ghost monitor (ghost_monitor.py)
def monitor_and_rescue_ghost_calls():
    # Find calls stuck >15 minutes
    stuck_calls = find_stuck_calls(threshold_minutes=15)
    
    for call in stuck_calls:
        # Verify with 3CX (similar to platinum's dual detection)
        is_active = check_3cx_call_status(call)
        
        if is_active is False:  # Confirmed ghost
            rescue_call(call)
```

### ✅ 3. Atomic Claim (Already Existed)

```python
# Alfred already had this pattern
call = db.scalar(
    select(Call)
    .where(Call.status == CallStatus.queued)
    .order_by(Call.scheduled_for)
    .limit(1)
    .with_for_update(skip_locked=True)  # PostgreSQL lock
)
if call:
    call.status = CallStatus.in_progress
    db.commit()
```

---

## What Alfred Could Still Improve

### 1. **Add Webhook Callbacks**

```python
# Future improvement: 3CX webhook endpoint
@app.post("/integrations/3cx/webhook")
async def threecx_webhook(event: ThreeCXWebhookEvent):
    if event.type == "call_ended":
        call = get_call_by_provider_id(event.call_id)
        if call:
            mark_call_ended(call, event)
    return {"status": "ok"}
```

### 2. **Sequential Mode Option**

```python
# Config option for sequential vs. concurrent
class GlobalSettings:
    concurrent_calling_enabled: bool = True
    max_concurrent_calls: int = 3
    
    # If disabled, use platinum-survey pattern
```

### 3. **Explicit Wait Pattern**

```python
# For critical campaigns, wait for completion
async def place_call_with_wait(call: Call):
    result = place_call(call)
    
    # Wait for completion (poll 3CX)
    for _ in range(60):  # 5 min max
        await asyncio.sleep(5)
        status = check_3cx_status(call)
        if status.ended:
            break
    
    return status
```

---

## Lessons Learned

### From Platinum-Survey Success:

1. **Simplicity prevents bugs** - Sequential is easier to reason about
2. **Redundancy is good** - Webhooks + polling = reliability
3. **Explicit > Implicit** - Wait explicitly, don't assume
4. **Defensive DB operations** - Always handle commit failures
5. **Log everything** - You can't debug what you can't see

### Alfred's Trade-offs:

✅ **Pros:**
- Concurrent calling = higher throughput
- 3CX native integration
- More features (DTMF routing, recording sync)

⚠️ **Cons:**
- More complex = more edge cases
- Harder to debug
- Ghost calls possible without monitoring

**Solution:** Keep concurrency for throughput, but add platinum-survey's safety patterns (ghost monitor, robust error handling, dual detection).

---

## Recommendations for Future Features

1. **Add webhook support** for instant call completion
2. **Implement sequential mode** for high-reliability campaigns
3. **Add circuit breaker** to pause on repeated failures
4. **Improve observability** with structured logging
5. **Add health checks** for each background service
6. **Consider call timeout enforcement** at DB level

---

## References

- **Platinum-Survey Repo:** https://github.com/Nisar222/platinum-survey
- **Alfred Ghost Fix Commit:** a7c8eff (2026-08-13)
- **Ghost Monitor Implementation:** ghost_monitor.py
- **Improved Dispatcher:** dispatcher.py (commit error handling)

---

## Conclusion

Platinum-survey's sequential, webhook-based approach is inherently safer but lower throughput. Alfred's concurrent approach is faster but requires robust monitoring and error handling.

**Best of both worlds:**
- Keep Alfred's concurrency for performance
- Add platinum-survey's safety patterns (ghost monitor, dual detection, robust error handling)
- Use sequential mode when reliability > throughput

**Result:** Fast, reliable, and observable system that handles edge cases gracefully.
