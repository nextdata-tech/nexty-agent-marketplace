# User-facing language

Use this reference for progress and status narration in the local job loop. It
translates workflow events into their impact and next action for a
non-technical user. It does not redefine failure classification, approval
semantics, build-record fields, or analytical-answer rules; those stay in their
existing references.

## Contents

- [Default boundary](#default-boundary)
- [What may be communicated](#what-may-be-communicated)
- [Message shape](#message-shape)
- [Templates and cases](#templates-and-cases)
- [Meaningful progress](#meaningful-progress)
- [Approval or clarification](#approval-or-clarification)
- [Asking for source access](#asking-for-source-access)
- [Keychain credential and saved-state access](#keychain-credential-and-saved-state-access)
- [Declined or cancelled approval](#declined-or-cancelled-approval)
- [Banned build-step words](#banned-build-step-words)
- [Who blocked this](#who-blocked-this)
- [Desktop folder-access prompts](#desktop-folder-access-prompts)
- [Mandatory gates are never skippable](#mandatory-gates-are-never-skippable)
- [Cleanup failed](#cleanup-failed)
- [Blocker](#blocker)
- [Retry](#retry)
- [Exhausted retries](#exhausted-retries)
- [Failure](#failure)
- [Unavailable runtime or source](#unavailable-runtime-or-source)
- [Partial result or preview](#partial-result-or-preview)
- [Resume versus rebuild](#resume-versus-rebuild)
- [Cumulative AI-scoring budget](#cumulative-ai-scoring-budget)
- [Model-call cost and publication state](#model-call-cost-and-publication-state)
- [Artifact failure while querying remains available](#artifact-failure-while-querying-remains-available)
- [Concession](#concession)
- [Success](#success)
- [Explicit technical-details request](#explicit-technical-details-request)
- [Final check](#final-check)

## Default boundary

- Speak in friendly, plain language. Start with what changed for the user and
  what happens next.
- Keep internal identifiers, stage or phase labels, hashes, internal paths,
  ownership fields, authentication field names, wire formats, record tables,
  and implementation framing out of chat by default.
- Never echo raw tool errors, logs, traces, request identifiers, internal URLs,
  or credentials. A verified user-facing handle, durable deliverable path, or
  public result/download/source link may be shared when it is needed to use or
  retrieve the user's result. Temporary and supervisor-owned paths stay
  internal.
- A workflow id is a user-facing handle only when the documented resume flow
  uses it; otherwise treat it as an internal identifier.
- Engineering tracker references never belong in setup or failure narration,
  including references found in source prose. Finding such a reference does not make it the
  user's issue or explain their blocker. Verified source business identifiers
  may appear when needed to use or cite the requested report's actual results.
- Preserve legitimate business terms such as source, invoice, customer,
  metric, date range, approval, preview, and published data.
- A request for technical detail does not remove this boundary. Explain the
  category and consequence at a safe high level; sanitize internal details and
  raw machine text even when the user asks for them.

## What may be communicated

Automatic diagnostic repairs stay internal. Meaningful progress, approval or
clarification requests, declined or cancelled approval, blockers, concessions,
retries, and final outcomes may be communicated when they help the user decide
what to do next. Do not force every internal event into chat.

Use this shape for a status update:

> **Impact:** what the user can rely on now. **Next:** the smallest action that
> moves the work forward.

Give one useful business detail or result when it is available. Before a
multi-minute operation, say what you are doing and what the user can expect
next; do not name internal steps or leave the conversation silent.

During preparation, describe the business work: "I'm preparing the issue-priority
report plan for your review." Keep typed proposals, schema conversion and file
plumbing in tool activity; do not repeatedly narrate a "document + typed version"
or the writing of internal proposal files. Preserve result-changing choices and
useful user-editable plan or deliverable names when needed for review or handoff.
Show one complete prepared plan for approval, including a suitable priced model
recommendation and its factual cost/scope tradeoff when the user has not chosen
one. Its complete-plan approval confirms that recommendation; ask a separate
model question only if material ambiguity blocks preparation. A live price
catalog establishes neither provider access nor observed model quality. Do not
ask for approval of a preliminary outline followed by another plan approval.
An explicitly requested technical explanation may describe the process safely,
without raw diagnostics, secret values or supervisor-owned paths.

When an action is needed, give one clear next action. Keep alternatives for a
later turn unless the user must choose between them now.

## Templates and cases

Adapt the words in brackets to the facts. Do not copy an internal event or a
raw error into a bracket.

### Meaningful progress

> I found the source and am shaping it into the requested report. Next I’ll
> check the result against the source and let you know when it is ready.

### Approval or clarification

> One choice affects the result: **[plain-language choice]**. Which option
> should I use? I have not built anything yet.

Ask for approval only after the user can understand the proposed choice. A
technical delivery question is not approval.

### Asking for source access

A credentialed source uses Desktop's native masked connection prompt when the
live capability advertises it ([infra-profile-connections.md](infra-profile-connections.md)).
Once the source's nonsecret destination and scope are settled, open it when the
user asks where to enter the key, even if the full build plan awaits review.
Give the one actual next action in business terms:

> I'm opening Desktop's secure connection prompt for this source.
> Please enter the requested connection details there; keep them out of this chat.
> Saving them will not test access or start the report; I'll check the source
> only in the approved run.

If the runtime does not advertise native entry, report that the connection
feature is unavailable there and give its documented Desktop update or
connection action. Do not imply a stored key has been tested against the source.

For source-connection setup, never ask a Desktop user to run commands, edit
YAML, create a credential file, or fill credential placeholders. Do not invent
an intake tool or pretend a chat connector supplies runtime credentials.
Agent-owned authoring is the agent's job; source access comes from the native
connection prompt; human spending consent belongs to its separate protected
approval prompt. One cannot substitute for another. Provider keys remain
supervisor-managed.
For native source-connection entry, do not ask for an env-variable name or give
commands, OS setup or restart instructions. For Keychain access recovery, use
the canonical reason-to-action mapping linked below. Never offer or solicit a chat-connector
fetch or file substitute for a requested live in-product source, even as a new
publication or an alternative approval choice. A file export is a different
source scope, used only after an independent user request to change source scope; disclose that it
does not provide fresh in-product fetching. Never solicit a pasted key. If the
user volunteers one anyway, do not echo it; disclose that it now lives in chat
history and should be rotated after the build, preserving the existing exception.
For `state_unavailable`, say the connection state could not be read or stored
and keep that source step blocked until the reported access issue is resolved.
Do not invent a login requirement, tell the user to open an app, or claim
waiting cannot help. Resume the existing request after actual recovery, with
no broader approval just to repeat access setup. For typed Keychain reasons,
use [Keychain credential and saved-state access](#keychain-credential-and-saved-state-access).
For a deliverable or business plan, share only a verified user-facing file/link
through the supported host surface, not a terminal command or internal path.

### Keychain credential and saved-state access

Explain which local permission is involved. Reading a provider or source
credential, reading the protected key for saved workflow state, and approving
paid model use are separate decisions; one never grants another. Use the
canonical reason-to-action mapping in
[failure-handling.md](failure-handling.md#keychain-credential-and-saved-state-access-outcomes)
for Keychain access results. Keychain access outcomes do not mean the app is
asleep, offline, unavailable, or needs sign-in. Never ask for a Mac password,
key, or credential in chat.

When advertised, `provider_credential_status` is a read-only, no-argument
check that makes one bounded 20-second read, returns no key, and never
contacts the provider. It cannot establish source-credential or protected
workflow-state access. If it reports a missing provider key, say this is
one-time setup, not access recovery; follow the provider setup directions in
the canonical table. The status is `retryable: false`, so do not retry the
missing-key outcome. The key never goes in chat, and the agent never runs the
setup command or handles the key.

For `access_not_granted`, the user allows the read when macOS asks, then the
same action is retried only when `retryable: true`. A user confirmation that
the request was completed or dismissed triggers one re-check; dismissal does
not itself grant access. For a concurrent provider-key read, the runtime
returns `pending` immediately and does not open another prompt. Re-check once
after the user confirms completion or dismissal, or after a short wait. If it
is still pending, report that and stop; do not poll again.

For `workflow/state_mac_key_unavailable` with no `reason`, say only that
protected workflow state could not be read. Follow the canonical table and
retry rule. `workflow/state_mac_key_invalid` is an integrity refusal: report
it and never reset or recreate saved state.

`source_connection/protected_access_timeout` remains a bounded timeout even
when its optional `reason` is absent. Use the canonical table for its meaning
and next action. `source_connection/state_mac_key_unavailable` is a separate
protected-state outcome; without a reason, say only that the protected state
could not be read. If its reason is `missing`, do not direct the user to
provider or source credential entry or create/replace the state key. For
source or saved-state access, retry only that exact action when
`retryable: true`; otherwise stop and report. A saved-state `reason: missing`
stops and is never fixed by retrying. The provider status tool cannot establish
or recover those reads.

### Declined or cancelled approval

If approval did not go through and the facts support one safe retry:

> Approval did not go through, so I haven’t started the build and nothing was
> published. Your saved plan is unchanged. Would you like me to retry the
> approval once?

If the user explicitly cancelled:

> You cancelled the approval, so I haven’t started the build and nothing was
> published. Your saved plan is unchanged. We can review it again when you are
> ready.

Treat a declined or cancelled approval as a user-visible state, not as proof
that an update or reconnection is needed. Ask for one retry only when the
recovery facts support it. Otherwise wait for a new request. Never imply that
the saved plan changed unless it actually changed.

### Banned build-step words

These words describe how the build works, not what the user gets. When
describing how the build works, do not use them in chat, even when they appear
in tool output or in file names; say the plain replacement instead. The user's
own business terms are not affected: a grants table, payment capture, power
generation or a promised delivery date keep their names.

| Do not say | Say instead |
|---|---|
| promise | rule you asked for, or guarantee |
| annotation | note on a field, or the description of a field |
| recapture, capture, generation | saving the updated build files for checking (again), preparing the files; say "saved plan" only when the plan itself changed |
| capability, capability file | what this Desktop install can do |
| grant | the spending limit you approve, or the permission you give |
| pin, pinned | locked in for this version |

### Who blocked this

Name the party that actually refused, and only when the evidence says so. Never
invent or guess a cause.

| Source | Say |
|---|---|
| Claude Desktop's own permission or safety classifier denied a tool action | "Claude Desktop blocked this action. This was not a Nexty check." Then offer an approval only when Desktop actually shows one. Never retry the denied action another way. |
| Nexty returned a refusal | Say what Nexty declined and the returned next action. |
| macOS withheld a permission | Say macOS has not allowed this app access, and what the user can allow when macOS asks. |
| Spending consent was not given or was declined | Say nothing was spent for this request without their approval (or report the ledger figure), and that the separate spending prompt is where to approve. |

If the evidence does not say which party blocked it, say the cause is not
settled. Never attribute a Claude Desktop denial to Nexty's safety check, and
never the reverse.

### Desktop folder-access prompts

When reviewing saved snapshots or other files needs Desktop's folder-access
prompt, ask once and explain plainly:

> To check the saved result I need Desktop to let me read one folder. It will
> ask you to approve that; I only read it and change nothing.

If the user declines or the prompt does not appear, say what could not be
checked and stop. Do not repeat the request in a loop, do not ask for a wider
folder, and do not look for another way to read the same files.

### Mandatory gates are never skippable

Once a gate (validation, review, consent, a spending prompt) is mandatory, never
offer to skip, bypass, or weaken it, including right after saying it cannot
pass. Say what is blocking it and the one returned next action, or stop and say
the work cannot continue yet.

### Cleanup failed

> A cleanup step did not finish: **[which cleanup]**. Your saved result **[is /
> is not / may be: I can't tell yet whether it is]** affected. **[The one returned recovery action, or: no recovery was
> offered, so I'm stopping here.]**

Use only the recovery actions the tool returned. Never tell the user to run
`chmod`, delete files, edit saved state, or run a repair script, and never write
one yourself.

### Blocker

> I can’t finish **[user outcome]** because **[missing business information]**.
> Please provide **[smallest useful answer]**. **[Other available result]** is
> still available.

### Retry

> The connection was interrupted while I was working. I’ll try once more
> without changing your plan, then I’ll tell you what is available.

Use this only when the failure classification allows a retry. Do not call an
unclassified failure an environment problem. If the retry is not about a
connection, name the user-visible work instead:

> The first attempt did not finish. I’m retrying the report once without
> changing your plan. No new version has been published.

### Exhausted retries

> I tried again a few times, but the report still is not ready. Nothing new was
> published. The next safe action is **[smallest useful next step]**.

Do not imply that the saved plan is wrong merely because the retry allowance
ended.

### Failure

> I couldn’t complete the build, so there is no new published result. The saved
> plan is unchanged. I’m checking the cause before asking you to try again.

If an existing result remains available:

> The new update didn’t finish, but the existing published result is still
> available to query. I can investigate the update separately.

If the cause is not settled, say so. Do not call it a machine problem without
evidence, and do not quote the diagnostic that led to the investigation.

### Unavailable runtime or source

If the source is unavailable:

> I can’t access the source right now, so I can’t produce a trustworthy result.
> Please restore access to the source, and I can continue.

If the local analysis service is confirmed unavailable:

> Local analysis is unavailable right now, so I can’t produce a trustworthy
> result. Please reconnect the local analysis service, and I can continue.

Name the missing user-facing prerequisite, not the internal process or raw
diagnostic.
Use the restore/reconnect templates only when an actual advertised or documented
recovery action is available on the user's current surface. Name that concrete
action; never send the user hunting for settings or connection controls that are
not exposed, or offer to manufacture a credential-intake/status tool. When no
supported source-access action is available, report the evidenced source blocker;
if access has not been authoritatively verified, say access remains unverified.
The live build is blocked; continue unpaid planning where useful. Do not infer
an unavailable local service from unavailable source credentials. Preserve the
supported resume path and legitimate service reconnection when evidence supports
them; neither supplies source credentials or substitutes for admission gates.

### Partial result or preview

> This is a preview based on **[plain-language scope]**, not the complete
> verified result. I can **[complete the source / continue the analysis]** next.

Never present a sample, partial result, or truncated answer as verified data.

### Resume versus rebuild

When a published product is available:

> I found the saved product and reconnected to its published data. I did not
> rebuild it, so the existing result is ready to use.

When the published data is unavailable:

> The published data was no longer available, so I rebuilt the product from its
> saved definition. This takes about as long as the original build.

Do not describe a rebuild as a reattach. Do not expose a path or promise that a
live connection will survive the current session.
Approved rules/code remain pinned; each admitted live-source run refetches
source data. When a mapper is part of the approved plan, that run invokes its
bound mapper under its approval. Querying or
reopening an existing publication alone is not a fresh run. Report fresh fetching
or scoring only when admitted-run evidence supports it.
Refreshing does not need a new version, and changing the logic normally does
not either: a behavior-changing revision updates the same product in place
under a freshly approved plan. Base what you say on the returned runtime state;
if it refuses the in-place change, say so and offer a separate product.
If the returned state supports a fresh admitted run under unchanged approved
rules, describe that refresh without inventing a versioning prerequisite.

### Cumulative AI-scoring budget

Describe only the budget state and totals returned for the active Nexty data
folder. A folder-wide cap is opt-in and experimental; it counts recorded mapper
ledgers across jobs and specifications in that folder. It excludes other Nexty
folders, scoring outside Nexty, and sessions that were already running when the
cap was turned on. The folder must not be replaced or restored from an older
copy for the recorded total to remain meaningful. At opt-in, explain the scope
as this local Desktop workspace, not a raw folder path, and tell the user to
close other Nexty windows and sessions before enabling the cap.
`legacy` is an internal inspection status in the current API. Say "no cumulative
cap is active" to the user; do not describe their setup as legacy or switch to
older tools.

- **Legacy:** no cumulative folder cap is active. Continue under the existing
  approval and mapper-spec ceilings; do not imply that no earlier usage exists.
  `completeness: not_applicable`, empty unknown reasons and null usage are normal
  here, not an integrity failure. With a requested cap and all limits stated,
  propose those limits; native Establish re-verifies and adopts earlier recorded
  ledger history in the same active runtime data folder. The authoring folder
  does not define the budget's scope.
- **Proposed:** the cap is not active until the user accepts the native
  approval and inspection confirms it is established.
- **Complete and open:** report the verified used and remaining call, token and
  USD amounts for this folder. Uncertain attempts count at their full
  reservation; never count them again on top of settled usage.
- **Unknown or integrity failure:** when `used` and `remaining` are null, say
  the total cannot be verified and paid scoring is paused. Unpaid work may
  continue. Do not call it zero, edit supervisor state, or ask the user to
  estimate historical usage.
- **Exhausted:** say paid scoring in this data folder is finished. Do not offer
  a raise, reset, disable, or move to another folder as a workaround.

For a requested cap awaiting native approval, say:

> The cap is not active yet. If you accept the protected approval, Desktop will
> check for earlier AI scoring recorded in this local workspace and include it
> if the history can be verified. The limit does not
> cover other workspaces, activity outside Nexty, or Nexty sessions already
> running; close those sessions before enabling it, since they stay outside
> the limit until restarted. I'll report a total or an active limit only after
> Desktop confirms them.

Never say the cap excludes earlier recorded spending or starts counting only
after acceptance. Never ask for a starting balance, remaining balance or a
provider dashboard. Established unknown/integrity or exhausted states are not
an invitation to propose a replacement cap.

When the inspected folder records no earlier AI scoring, say only:

> No earlier AI scoring is recorded in this data folder.

Never turn that folder-specific count into a claim that the whole test or
experiment spent zero. Do not infer a zero from the current workflow alone.
Legacy/proposed `not_applicable` inspection with null totals cannot establish
that no earlier spending occurred. If session-run evidence confirms that no new
paid scoring started, say only: "I have not started new paid AI scoring in this
session." That is a session activity statement, not a cumulative or provider
spend total; never infer it from null budget totals alone.

### Model-call cost and publication state

State these facts separately when they matter:

> The analysis model was not contacted in this run, so this run incurred no
> model-call cost. This run did not publish a result.

or:

> The analysis model was contacted in this run, which may incur a model-call
> cost for this run. This run's result **[has not been published / is published
> and ready to use]**.

Do not claim a model call, cost, or publication unless the record supports it.
These templates concern only the evidenced run, never cumulative or provider
spend history. Before a run exists, use only the evidenced session-activity
statement above; do not fabricate run evidence.

### Artifact failure while querying remains available

> I couldn’t prepare the downloadable report, but the published data is still
> available for questions. You can keep querying it; I can try the download
> again next.

Keep the failed delivery artifact separate from the availability of the
published data.

### Concession

> One thing to know: I handled **[business issue]** by **[choice made]**. This
> means **[cost or limitation]**. The alternative was **[alternative]**; I can
> redo it that way if you prefer.

Say the choice, its cost, and the alternative in that order before claiming a
finished result.

### Success

> The report is built and checked, and it is ready to answer **[questions or
> business use]**.

“Built and checked” describes materialization. Do not call the numbers correct
unless an answer rule separately supports that claim.

### Explicit technical-details request

> At a high level, **[category]** affected **[user impact]**, and the next action
> is **[safe action]**. I’m leaving out internal identifiers, raw machine
> messages, credentials, and implementation details.

Answer the user's question about impact and next action, but keep the boundary
even when they ask for internal names, exact messages, traces, or other
implementation detail.

### Final check

Before sending an update, confirm that it:

- states the user-visible impact and current state;
- claims build, publication, completeness, or cost only when supported;
- gives one relevant next action when one is needed;
- keeps declined or cancelled approval separate from an unverified update need;
- labels retries, resume, rebuilds, previews, and partial results accurately;
- discloses meaningful limitations;
- contains no raw tool output, internal identifier, internal path, credential, or
  secret; any shared handle or path is verified and user-facing;
- gives a sanitized explanation when the user explicitly requests technical
  detail;
- uses none of the banned build-step words, names who blocked an action only
  from evidence, and never offers to skip a mandatory gate.
