#!/usr/bin/env python3
"""Manually start background threads if they're not running."""
import sys
sys.path.insert(0, '/app')

import threading
import time
from app.recording_sync import RecordingSync
from app.dispatcher import CampaignDispatcher
from app.transcript_sync import TranscriptSync

# Check existing threads
existing_threads = [t.name for t in threading.enumerate()]
print(f'Current threads: {existing_threads}')

# Start missing threads
if 'campaign-dispatcher' not in existing_threads:
    print('Starting campaign-dispatcher...')
    dispatcher = CampaignDispatcher()
    dispatcher.start()

if 'recording-sync' not in existing_threads:
    print('Starting recording-sync...')
    recording_sync = RecordingSync()
    recording_sync.start()

if 'transcript-sync' not in existing_threads:
    print('Starting transcript-sync...')
    transcript_sync = TranscriptSync()
    transcript_sync.start()

time.sleep(2)

# Verify
final_threads = [t.name for t in threading.enumerate()]
print(f'\nThreads after start: {final_threads}')
print('\n✓ Background threads are now running')
