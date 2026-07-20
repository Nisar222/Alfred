# Jamal Dialler UX specification

## Purpose and design principle

Jamal Dialler is a daily operating tool for a business owner, not a CRM or an analytics product. The interface must make the next useful action obvious and keep all technical setup, model language, and detailed reporting out of the normal working path.

The primary daily loop is:

```text
Launch campaign → calls run → review completed calls → system learns → approve tomorrow's script
```

### Product rules

- Show one primary next action at a time.
- Use plain language: **Calls running**, not `queue active`; **Needs review**, not `unlabelled completed calls`.
- Default complex configuration; move exceptions to an Admin area.
- Never require outcome labels during a live call. Review happens afterward, one call at a time.
- Treat a label as a simple business decision, not a QA form.
- Keep real customer data inside Jamal's environment; show a quiet “Private server” status rather than security implementation details.
- The product may suggest a script improvement, but it must not silently change a live script in the MVP.

## Users and journeys

### Jamal — owner/operator (primary)

Jamal operates the system in five minutes or less on most days.

1. Opens **Today**.
2. Sees the single action: start calls, review calls, or approve tomorrow's change.
3. Starts a prepared campaign if calls have not begun.
4. Reviews only calls without an outcome label.
5. Reads one clear learning insight and approves or keeps the existing script.

### Administrator (secondary, infrequent)

An administrator creates campaigns, imports lists, adjusts calling windows, checks integration health, and views detailed history. These controls must be separate from the daily home screen.

## Information architecture

| Area | Purpose | Primary user | Frequency |
| --- | --- | --- | --- |
| Today | Daily progress and one next action | Jamal | Daily |
| Review calls | Listen/read and label a completed call | Jamal | Daily |
| Campaigns | Create, prepare, launch, pause, and inspect campaigns | Jamal/Admin | Weekly/daily |
| Learnings | Review evidence and approve a proposed script version | Jamal | Daily/weekly |
| History | Search completed calls and outcomes | Admin | As needed |
| Settings | Calling hours, retention, integrations, users | Admin | Rarely |

Navigation uses a left rail on desktop and a bottom bar on tablet. The daily routes are first: **Today**, **Review**, **Campaigns**. **Learnings** gets a badge only when a decision is needed. Hide Settings behind an “Admin” menu.

## Screen specifications

### 1. Today (default landing screen)

**Goal:** answer “What should I do now?” within three seconds.

#### Layout

1. Header: Jamal Dialler; discreet green dot + “Private server connected”.
2. Large daily status card.
3. One contextual next-action card with one filled primary button.
4. Two short secondary cards: today’s results and system learning.
5. Recent activity, limited to five rows.

#### Daily status card

When a campaign is running:

```text
Today's campaign                         Calls running
68 of 100 calls complete
[progress bar] 68%
4 sales · 11 leads · 46 not interested · 7 wrong numbers
                                                   [Pause calls]
```

The counts are tap/click targets only in the history view; they do not turn the home page into a report.

#### Next-action states and exact copy

| Condition | Title | Supporting copy | Primary action |
| --- | --- | --- | --- |
| No prepared campaign | Ready to make calls? | Create a campaign and add the people you want to call. | Create campaign |
| Prepared campaign; no calls started | Today’s calls are ready | 100 people are ready to call between 9:00 AM and 6:00 PM. | Start today’s calls |
| Calls running; review items exist | 12 calls need your review | Your labels teach the system what worked. This takes about 3 minutes. | Review now |
| Calls running; no review items | Calls are working | We’ll let you know when a call is ready for review. | View live calls |
| Calls complete; review items exist | Finish today’s review | 12 calls still need an outcome. | Review 12 calls |
| All reviewed; proposed change exists | Tomorrow’s improvement is ready | Asking a discovery question before the pitch led to more leads today. | Review improvement |
| All reviewed; no proposed change | You’re ready for tomorrow | Today’s calls are reviewed and your current script remains active. | View results |
| Calls paused | Calls are paused | No new calls will start until you resume. | Resume calls |

#### Learning card

Use one conclusion, one comparison, and one low-risk action. Example:

> **The system noticed a pattern**  
> Calls that asked at least two discovery questions made 18% more leads.  
> [Review tomorrow’s script]

If evidence is weak, say: “The system needs more labelled calls before it can recommend a change.” Never present weak correlation as a certainty.

### 2. Review calls (focused workspace)

**Goal:** apply an accurate outcome with the least thought and no table scanning.

Open from **Review now**. Present one call at a time, with a clear `3 of 12` progress indicator and a “Finish later” control. Do not allow accidental loss of position.

#### Layout

```text
← Today                         Review calls                         3 of 12
____________________________________________________________________________
Sarah M. · +971 … · September outreach                  02:41 completed
[ audio timeline / Play / speed / volume ]

Conversation
  Agent: …
  Sarah: …
  Agent: …

What happened on this call?
[ Sale ]     [ Lead / call back ]     [ Not interested ]     [ Wrong number ]

                         [ Skip for now ]
```

#### Label behaviour

| Button | Result | Confirmation/next step |
| --- | --- | --- |
| Sale | `sale` | “Marked as sale” then advance after ~500 ms |
| Lead / call back | `lead` | Reveal optional single field: “When should we call back?”; save and advance |
| Not interested | `reject` | “Marked as not interested” then advance |
| Wrong number | `wrong_number` | “Marked as wrong number” then advance |
| Skip for now | no label | Keep in queue, advance without warning |

Outcome buttons must be large, always visible below the transcript, keyboard accessible, and distinguishable by text as well as colour. The owner can change the latest label from the call detail page.

#### Empty and failure states

- Queue empty: “All caught up. Every completed call has an outcome.” Primary action: **Back to Today**.
- No recording: keep transcript and labels available; show “Recording unavailable for this call.”
- No transcript: play recording if available; show “Transcript is still being prepared.”
- Save failed: retain the selected label locally, show “We couldn’t save that label. Try again.” Do not advance.

### 3. Campaign setup (guided, three steps)

**Goal:** get from a prospect list to a safe, prepared campaign without exposing operational complexity.

Use a progress header: `1 People → 2 Conversation → 3 Schedule`. Autosave each completed step. The campaign remains **Draft** until the owner selects **Ready for calls**.

#### Step 1 — People

Title: **Who should we call?**  
Copy: “Upload a CSV list with names and phone numbers. We’ll check it before any calls are made.”

- Drag-and-drop CSV uploader and “Download sample CSV” link.
- Preview first five contacts; show valid, duplicate, and incomplete totals in plain language.
- Blocking validation only for missing/invalid phone numbers; allow the owner to continue after excluding invalid rows.
- Completion CTA: **Continue to conversation**.

#### Step 2 — Conversation

Title: **What should the caller say?**  
Copy: “Start from your approved script. You can change it later; every change is saved.”

- Script name and editable script text.
- Optional “Suggested structure” checklist: greeting, reason for calling, discovery question, benefit, next step.
- Do not show prompt variables, model controls, tokens, or agent temperature.
- CTA: **Continue to schedule**.

#### Step 3 — Schedule and review

Title: **When should calls happen?**  
Copy: “We will only call during these hours.”

- Country/timezone, calling days, start/end time; sensible default based on account location.
- Read-only review: people count, script name, schedule, concurrent-calls limit (display `Up to 8 calls at once`).
- Required acknowledgement: “I have permission to contact these people and use this script.”
- CTA: **Make campaign ready**. Success screen: “Your campaign is ready. Start it from Today when you’re ready.”

### 4. Learning approval

**Goal:** safely turn labelled outcomes into a better next-day script.

Show a compact before/after script comparison, evidence, and reversible decision.

```text
Suggested improvement for tomorrow
Add this question before presenting the offer:
“What are you currently using for …?”

Why: calls with this question produced 9 leads from 42 calls,
compared with 5 leads from 48 calls without it.

[Use this tomorrow]  [Keep current script]  [Review evidence]
```

- Default to **Keep current script** when fewer than the agreed minimum number of labelled calls supports the recommendation.
- “Use this tomorrow” creates a new script version and schedules it for the named campaign; it never overwrites history.
- “Review evidence” opens a filtered call list, not raw model reasoning.
- Clearly state the active version: “Today’s calls use: September script v3.”

### 5. Campaign and history views

Campaign cards show name, status, scheduled window, people count, completion count, and one contextual action (Start, Pause, Resume, or View). Avoid multiple equal-weight buttons.

History is deliberately secondary. It supports filtering by date, campaign, and outcome; it exposes transcript, recording availability, call duration, label history, and active script version. It is not part of the daily path.

## Shared visual and interaction direction

- Calm light background, dark text, generous whitespace, and one high-contrast primary colour. Reserve red for destructive/error states.
- Use sentence case and familiar verb labels: **Start calls**, **Review now**, **Pause calls**.
- Do not rely on colour alone for status. Pair with a word and icon/shape.
- Desktop target: 1280 px wide. Ensure the primary action is visible without scrolling. Tablet target: 768 px wide. The review outcome bar remains visible.
- Minimum touch target: 44 × 44 px; outcome buttons should be at least 56 px high.
- Focus states, semantic buttons/labels, transcript headings, and keyboard playback must meet WCAG 2.2 AA expectations.
- Ask for confirmation only for high-impact actions: launching a campaign, pausing/resuming, and applying a script change. Do not confirm ordinary outcome labels.

## Status model and data display rules

| Domain status | User-facing label | Allowed action |
| --- | --- | --- |
| Campaign draft | Draft | Continue setup |
| Campaign ready | Ready to call | Start calls |
| Campaign active | Calls running | Pause calls |
| Campaign paused | Calls paused | Resume calls |
| Campaign completed | Finished | View results |
| Call queued/in progress | Calling | View only |
| Call completed, no outcome | Needs review | Review |
| Call completed, labelled | Reviewed | View/change label |
| Recording/transcript pending | Preparing call details | Label remains available |

Never reveal an internal state name or an error trace to Jamal. System-wide problems use: “Calls need attention. No new calls will start until this is fixed.” with **View details** restricted to Admin.

## MVP acceptance criteria

### Daily flow

- On opening Today, an owner can identify the single next action in under three seconds.
- An owner can start a prepared campaign in two actions or fewer, after an explicit confirmation.
- An owner can label a normal completed call in one click/tap from the review workspace.
- Review progress is accurate and skipped calls remain in the review queue.
- When all review items are labelled, the Today action changes without a manual refresh.

### Campaign setup

- A nontechnical owner can create a ready campaign from a valid CSV in under five minutes without visiting Settings.
- Invalid or duplicate contacts are explained in human language before a campaign can launch.
- A campaign cannot launch without calling hours and the permission acknowledgement.

### Learning

- A proposed script change includes the exact text change, evidence, campaign, and effective date.
- Approving a change creates a new version; existing and historical calls retain their previous version association.
- The interface never promises improvement or recommends a change without sufficient labelled evidence.

### Reliability and accessibility

- Failed label saves do not discard the chosen outcome or advance the review queue.
- The primary daily controls and all outcome labels work with keyboard and screen reader navigation.
- All core states have clear empty, loading, offline/error, and success feedback.

## Implementation mapping for the current scaffold

The current single-page scaffold already exposes campaign creation, launch/simulation, daily metrics, and inline outcome controls. The implementation should evolve it in this order:

1. Make **Today** the default route and replace the dense metric row with daily status + next action.
2. Move outcome controls out of the call table into the focused **Review calls** route.
3. Replace the campaign form with the three-step setup flow; keep the existing campaign API as the initial integration point.
4. Add campaign/call status wording from the table above, then learning approval once versioned recommendations are available.
5. Keep detailed metrics and raw call lists in History/Admin, not on the landing screen.
