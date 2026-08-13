# Efficiency Guide: Working with Cursor Agents

## The 60% Slowdown Problem

**Issue:** Copy-pasting between agent and VPS kills productivity.

**Solution:** Make agents fully autonomous.

---

## Best Practices

### 1. Enable Agent SSH Access

Make sure your SSH key is set up so agents can connect directly:

```bash
# Test from your terminal
ssh nisar@165.154.217.39 "echo connected"
# Should work without password
```

If agents have the same SSH access as you, they can work autonomously.

### 2. Use Subagents for VPS Operations

When you need VPS work done:

```
❌ DON'T: "Can you check the logs?"
         → Agent asks you to copy-paste
         → You SSH manually
         → Copy-paste output back
         → 60% slower

✅ DO: "Check the logs"
      → Agent launches shell subagent
      → Subagent SSHs directly
      → Returns findings
      → No copy-paste needed
```

### 3. Set Expectations Up Front

In your initial prompt:
```
"You have SSH access to the VPS. Use it directly.
Never ask me to run commands. If your SSH fails,
launch a subagent immediately."
```

### 4. Use Multi-Agent Pattern for Complex Tasks

```
User: "Fix the recording issue"

Agent: 
├─ Subagent 1: VPS diagnostics (SSH to VPS)
├─ Subagent 2: Code analysis (local)
└─ Parent: Synthesizes findings, implements fix

Result: Parallel investigation, faster resolution
```

### 5. Create Automation Scripts

For repetitive tasks, create scripts agents can use:

**Example: `/Users/nisarkhan/Documents/dev2/Alfred/scripts/vps-status.sh`**
```bash
#!/bin/bash
ssh nisar@165.154.217.39 << 'EOF'
cd ~/apps/alfred
echo "=== Containers ==="
docker compose ps
echo ""
echo "=== Recent Logs ==="
docker compose logs api --tail=50
echo ""
echo "=== Database Stats ==="
docker compose exec -T db psql -U alfred -d alfred -c "
  SELECT 
    (SELECT COUNT(*) FROM calls WHERE status='completed') as completed,
    (SELECT COUNT(*) FROM recordings) as recordings;
"
EOF
```

Then agents just run: `./scripts/vps-status.sh`

### 6. Use Screen Sharing Only for Debugging

Reserve screen sharing/copy-paste for:
- Debugging agent issues
- Verifying visual UI changes
- Final user acceptance testing

**Not for:** Routine commands, logs, diagnostics

### 7. Designate a "VPS Agent"

If you have persistent agent issues:
- Keep one agent window connected to VPS
- Have main agent delegate VPS tasks to that agent
- Main agent reads the VPS agent's transcript for results

### 8. Use Watch Commands for Monitoring

Instead of repeatedly checking status:

```bash
# On VPS terminal (keep open in side pane)
watch -n 5 'docker compose logs api --tail=20 | grep -i error'
```

Agent sees live updates without asking.

---

## Efficiency Workflow

### Traditional (60% slower):
1. Agent: "Can you check X?"
2. You: SSH to VPS
3. You: Run command
4. You: Copy output
5. You: Paste to agent
6. Agent: Analyzes
7. Agent: "Can you check Y?"
8. Repeat...

### Optimized:
1. Agent: "Checking X..." [launches subagent]
2. Subagent: [SSHs, runs commands, returns data]
3. Agent: "Data shows... Checking Y..." [launches another subagent]
4. Both subagents work in parallel
5. Agent: "Root cause found. Implementing fix..."
6. You: [Drink coffee]

---

## Time Savings

**Before:** 
- 10 diagnostic commands
- 2 minutes per command (SSH, copy-paste, wait)
- = 20 minutes just for diagnostics

**After:**
- Agent launches 3 parallel subagents
- Each runs multiple commands autonomously
- = 3-5 minutes total

**Savings:** 15 minutes per investigation × multiple investigations = Hours saved

---

## Emergency Fallback

If agent SSH consistently fails:
1. Open separate terminal to VPS
2. Keep it running
3. When agent needs data, you have VPS ready
4. But this should be rare - report SSH issues to Cursor

---

## Your Setup Checklist

- [ ] SSH key-based auth working (no password prompts)
- [ ] Agents know they can SSH directly
- [ ] Initial prompts set expectations for autonomy
- [ ] Handoff docs exist for context preservation
- [ ] Frequent commits to avoid context loss
- [ ] Subagents used for parallel work
- [ ] Scripts created for common tasks

---

## The Golden Rule

**"If I have to copy-paste, the agent isn't being autonomous enough."**

Train agents to work independently. Your job is to review and approve, not to be their hands.
