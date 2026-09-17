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
- [Asking the user to open a file](#asking-the-user-to-open-a-file)
- [Declined or cancelled approval](#declined-or-cancelled-approval)
- [Blocker](#blocker)
- [Retry](#retry)
- [Exhausted retries](#exhausted-retries)
- [Failure](#failure)
- [Unavailable runtime or source](#unavailable-runtime-or-source)
- [Partial result or preview](#partial-result-or-preview)
- [Resume versus rebuild](#resume-versus-rebuild)
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
  ownership fields, and implementation framing out of chat by default.
- Never echo raw tool errors, logs, traces, request identifiers, internal URLs,
  or credentials. A verified user-facing handle, durable deliverable path, or
  public result/download/source link may be shared when it is needed to use or
  retrieve the user's result. Temporary and supervisor-owned paths stay
  internal.
- A workflow id is a user-facing handle only when the documented resume flow
  uses it; otherwise treat it as an internal identifier.
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

### Asking the user to open a file

> Your API key has to come from you, so I've left a placeholder for it. Run this
> to open the file:
>
> ```
> open -e /Users/you/nxd-jobs/orders/infra-profile.yaml
> ```
>
> Find the line `auth_token: REPLACE_ME`, put your key where `REPLACE_ME` is,
> save, and tell me when you're done.

**Never leave the user to find or open the file themselves.** Naming a path
assumes they know how to reach it, which editor opens a `.yaml`, and that they
are comfortable in a terminal. Give a runnable command on its own line so it can
be clicked or copied with no other knowledge:

- macOS: `open -e <absolute path>`. The `-e` forces TextEdit, rather than
  whichever application happens to claim the extension; a `.yaml` can otherwise
  open in Xcode or in nothing at all.
- Linux: `xdg-open <absolute path>`.
- To show it in a file browser instead of opening it: `open -R <absolute path>`.

Then name the exact field to change and what to change it to, and say to save and
come back. The command carries a path, never a secret, so it is safe in
transcript. This applies to every file the user is asked to edit, not only
credential files.

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

### Model-call cost and publication state

State these facts separately when they matter:

> The analysis model was not contacted, so there was no model-call cost. Nothing
> was published.

or:

> The analysis model was contacted, which may incur a model-call cost. The
> result **[has not been published / is published and ready to use]**.

Do not claim a model call, cost, or publication unless the record supports it.

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
  detail.
