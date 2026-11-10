---
name: envision-contact-sourcing
description: Find, verify and cross-check marketing contacts and emails for a list of Envision brand partners. Use when asked to "find contacts for these companies/brands", source social/marketing contacts, or build an outreach contact list for an Envision campaign. Accuracy first, then speed.
---

# Envision Contact Sourcing (accuracy first, then speed)

Input: a list of brands (Brand, Tier, Website). Output: a pre-filled review sheet, one row per contact. Nothing goes into HubSpot until approved.

## Step 1: Batch planning, ownership and HubSpot checks
- Start with the existing brand list (Brand, Tier, Website); keep this input list separate from the exact 14-column prospecting review sheet. Do not replace the established priority order or review-sheet schema.
- Work in batches of 10 companies in the existing company-list priority order. Complete and review one batch before starting another; do not invent a new priority order. Flag a final partial batch for Roxy's direction rather than inventing companies.
- Before researching contacts, confirm brand ownership, parent/operator and actual employee email domain using evidence. A website domain alone does not establish the employee email domain, especially after an acquisition. Record evidence, conflicts and routing decisions in Ownership / Routing Notes; leave unresolved facts explicitly pending.
- Check HubSpot first by company name and website domain and inspect associated contacts. Match identities and known email candidates; do not add existing contacts as new prospects. Flag ambiguous matches rather than assuming absence.
- Flag anyone who has replied before for review and preserve their sourcing priority; prior replies do not authorize repeat outreach or bypass suppression checks.
- These are research instructions, not evidence that a HubSpot integration or property exists. If access or required information is unavailable, log the unresolved check; do not claim it passed.

## Step 2: Find relevant current contacts
- Source and rank 3–5 qualified current contacts per brand using the confirmed sourcing requirements below. For a small company with only one marketing person, include that person and the owner/founder as contact #2, with evidence and the exception noted. Never invent people or include irrelevant roles to fill a quota; log genuine shortfalls.
- Prioritize senior marketing contacts, brand heads, and marketing/brand directors. Social media, influencer and community managers/coordinators are secondary options; do not use the additional reference's social-media-first order. Apply the following role order within these confirmed priorities:
  1. Brand Director
  2. Head of Brand / Brand Lead
  3. Senior Brand Manager / Brand Manager
  4. Marketing Director
  5. Marketing Manager or higher
  6. Social Media Lead / Manager / Coordinator (secondary)
  7. Influencer Marketing Lead / Manager / Coordinator (secondary)
  8. Community Lead / Manager / Coordinator (secondary)
  9. Marketing Partnerships / Brand Partnerships
  10. Growth / Digital / Ecommerce leadership
- Founder, CMO or Chief Brand Officer may be included only under Roxy's stated exceptions. The small-company owner/founder exception above is explicit; other exception criteria are not supplied here. Ask Roxy to confirm them before including other executive exceptions; do not infer exceptions from seniority or a contact shortfall.
- Exclude sales-only, finance, customer support, engineering/IT/data, general management, operations, HR, design-only, unrelated partnerships and executives outside the confirmed exceptions.
- Run an AI contact search once per brand using the confirmed brand/parent routing, Step 2 role priorities and exclusions, and the current contact-count requirement. Record the actual sources, not unsupported AI assertions.
- Research the confirmed parent/operator where appropriate and document the routing. For every candidate, capture full name, current role, function, personal LinkedIn profile URL and role evidence. Do not guess. Never invent names, job titles or LinkedIn profiles; never present a fabricated email as discovered evidence. Pattern-derived candidates are permitted only as explicitly labeled hypotheses under Step 6, pending the decision log below.

## Step 3: Check each name on LinkedIn
- Confirm current employment, role and company using each person's own LinkedIn profile. Record the personal profile URL in LinkedIn and the role evidence URL in Role Source URL, with the research date.
- Still in a relevant current role: keep. Left or changed to an irrelevant role: remove from the candidate shortlist, log `Departed-Role Changed`, and research the current role holder.
- If Apollo and LinkedIn disagree, use LinkedIn as the primary employment source. Flag unresolved identity or employment ambiguity in the unresolved log; do not treat it as confirmed.

## Step 4: Apollo search and reveal
- Search Apollo with the same established roles, exclusions and brand/parent routing used for the AI search. Use the confirmed senior-marketing-first priority and secondary social/influencer/community options in Step 2.
- Check any new names on LinkedIn (as in Step 3).
- Reveal emails only for people who pass the LinkedIn check.

## Step 5: Cross-reference the names
- Compare the AI and Apollo findings for each brand. Found by both AI and Apollo, confirmed on LinkedIn: strongest supporting evidence, not automatic outreach eligibility.
- Found by only one source: keep only if LinkedIn confirms.
- Sources name different people for the role: use each person's LinkedIn profile as primary evidence; log unresolved ambiguity.
- Source and rank 3–5 qualified current contacts per brand using Step 2's confirmed role priorities, preserving the documented small-company exception and honest shortfalls. Check for duplicates within the batch, earlier batches and existing HubSpot contacts.

## Step 6: Find and cross-check the emails
Run the helper commands below from the repository root, subject to the existing paid-credit approval rules. Codex helpers use only the configured `RELAY_TOKEN` through `https://envision-email-relay.onrender.com`; never print its value. Provider keys belong on the relay, not in Codex. Read the provider result in the returned JSON; `ok: true` alone does not establish email validity.
Use only individual professional email candidates. Never use shared inboxes (including info@, hello@, press@ or team/department addresses). Leave Professional Email Candidate blank when no personal email is found; never insert a placeholder or a shared inbox.

1. **HubSpot:** inspect available existing contact email and reply history first, especially previous replies. Prioritize that email as a candidate; prior replies do not bypass verification, suppression, recontact review or approval. If history is unavailable, record the gap rather than assuming no previous outreach.
2. **Apollo:** the email revealed in Step 4.
3. **AI anchor search, once per company:**
   - Find one real, published email of a named staff member. Generic inboxes (info@, hello@, press@) don't count.
   - Look in press releases, recall notices, trademark filings, team pages, German Impressum pages, LinkedIn posts.
   - Record the email domain (often different from the website), the pattern (first.last@, flast@, first@) and the source link.
   - Run at least 10 searches before calling it "not found".
   - Build each person's email from that pattern and label its source `pattern-inferred`, never automatically confirmed. Keep this source label even if later verification makes the address eligible.
4. **Findymail:** search by name and company domain.
   - Run `python3 scripts/findymail.py find --name "<full name>" --domain "<company domain>"`. Compare the result with the Apollo candidate and public anchor; retain the source evidence for each.
5. **Compare:**
   - All agree: record source agreement; agreement alone does not confirm deliverability or outreach eligibility.
   - Disagree: prioritize the email matching the anchor for verification, but retain unresolved conflicts as risk flags. If reliable evidence cannot resolve a disagreement, mark the contact unresolved and log it; do not select an address by guesswork, even when an anchor exists. No anchor: flag it, don't guess.
   - Only Apollo has one: keep verification status `unverified`; use exactly one Email Confidence label under Step 7 based on documented evidence. Do not invent a label or treat Apollo discovery as positive verification.
6. **NeverBounce:**
   - Run `python3 scripts/neverbounce.py --email "<email>"`.
   - Record the actual provider result and verification date. A positive `valid` result is necessary evidence, but does not bypass the eligibility checks below.
   - Invalid: drop from email outreach. Unknown, catch-all, failed verification, missing results or transport errors do not qualify an address for email outreach. Keep unresolved cases pending.
   - Unknown or catch-all: obtain the required paid-credit approval before a second check with Findymail's verifier.
     Run `python3 scripts/findymail.py verify --email "<email>"` for that second check.
   - A Findymail second check must not automatically override unresolved catch-all or unknown risk. Preserve both provider results and any disagreement; a published anchor supports address sourcing, not proof of deliverability.
7. **Email outreach eligibility:**
   - Mark an address eligible only when it has a positive verification result, the person's current role and company have been confirmed with evidence, and no unresolved risk flags remain.
   - Pattern inference, source agreement, prior replies, or a positive second check alone cannot satisfy these conditions. Document evidence resolving each risk; otherwise leave email outreach pending/ineligible.
   - Eligibility is a review flag, not permission to send or update records. Roxy reviews first, then Bridget; existing paid-credit and HubSpot approval requirements remain in force.

## Step 6a: Select the outreach route and check Dripify (read-only)
- Preserve all sourcing, LinkedIn confirmation, ranking, email verification and approval rules above.
- If a usable verified email exists under Step 6, prioritize **email outreach**. Apply all three Step 6 eligibility conditions: positive verification, confirmed current role/company, and no unresolved risk flags. Unverified, invalid, unknown, catch-all or failed results do not automatically qualify; anchors and second checks do not bypass these conditions.
- If email outreach is selected, do not send that contact to Dripify, including for lookup. Record route `email` and Dripify status `not checked — email selected`.
- Otherwise, check existing Dripify leads by the confirmed LinkedIn URL first:
  `python3 scripts/dripify.py search --linkedin-url "<LinkedIn URL>"`.
- If LinkedIn finds an existing lead, flag `existing in Dripify`; do not duplicate or enroll them.
- Only after a successful, explicit no-match LinkedIn result, use any known email as a secondary duplicate lookup:
  `python3 scripts/dripify.py search --email "<known email>"`.
  An unverified email may identify an existing lead; this does not make it usable for email outreach.
- If either lookup finds the contact, flag `existing in Dripify`. If LinkedIn and any available secondary email lookup explicitly find no match, mark `eligible for Dripify outreach`.
- Missing or unconfirmed LinkedIn URL, errors, provider failures, incomplete/paginated results or ambiguous responses mean `Dripify check pending`; do not infer absence or eligibility. Resolve all relevant result pages before declaring no match.
- Read provider data, not just transport `ok`. Record the lookup identifier, result and date checked. If response semantics are unclear, leave pending for review.
- Campaigns can be listed for reference with `python3 scripts/dripify.py campaigns`.
- These helpers call only `GET /dripify/campaigns` and `POST /dripify/leads/search` on the relay using `RELAY_TOKEN`. Never call Dripify directly or require `DRIPIFY_API_KEY` in Codex.
- Eligibility is a review flag only. Do not create leads, add anyone to a campaign, enroll contacts, send outreach or make any external write operations in this integration. Existing approvals still apply to subsequent work.

## Step 7: Prospecting review sheet, evidence and unresolved log
Use exactly these columns in this order; do not append verification or approval columns to this sheet:

Batch, Company, Parent / Operator, Priority, Full Name, Current Role, Function, Professional Email Candidate, Email Confidence, LinkedIn, Role Source URL, Email Pattern Source URL, Ownership / Routing Notes, Research Date.

- One row per contact. Use the existing company priority, the confirmed parent/operator, personal LinkedIn URL, role evidence and email-pattern source where available. Do not fabricate evidence URLs. Record ownership/domain evidence and unresolved routing details in Ownership / Routing Notes.
- Apply exactly one of these five Email Confidence labels, verbatim:
  - Publicly confirmed company email; re-verify before sending
  - Pattern-derived; verify before sending
  - Low-confidence pattern; sources conflict, verify before sending
  - Pattern-derived; post-acquisition domain must be verified
  - No verified personal email found
- Use the public-confirmation label only with evidence for the individual's company email; a published pattern for another employee does not establish it. Pattern candidates remain `pattern-inferred` in the supporting evidence, never automatically confirmed.
- If no personal email candidate is found, leave Professional Email Candidate blank and use `No verified personal email found`. Do not erase an existing candidate merely because its verification is pending; confidence describes sourcing evidence, not deliverability. If the supplied labels do not unambiguously fit the evidence or overlap, ask Roxy to resolve the classification; do not invent labels or claim public confirmation.
- Keep a separate linked verification/eligibility record keyed by Batch, Company, Full Name and LinkedIn (include the exact email under review). Preserve AI/Apollo comparison details (found by both or one source, disagreements and LinkedIn resolution), historical outreach/reply evidence and gaps, address source, verification status (positive / invalid / unknown / catch-all / failed / unverified), verification dates, provider names and exact results from every check, evidence, source conflicts, role/company confirmation evidence and dates, unresolved risks and resolution evidence, route, Dripify lookup results/dates and eligibility reasons. Keep `pattern-inferred` provenance separate from the exact Email Confidence labels. Ask Roxy where to keep this supporting record if no location is established.
- No Email Confidence label, discovered address or pattern alone authorizes outreach. Publicly confirmed addresses must also be re-verified before sending under Step 6; stale/missing verification or employment evidence remains unresolved. No automatic freshness period is defined here.
- Maintain an unresolved log with: Campaign, Batch, Company, Full Name, Role, LinkedIn URL, Reason unresolved, Date, Case Type (where applicable). Case Type values: Identity Ambiguous, Data Corruption, Departed-Role Changed, Other. Ask Roxy to confirm the log location before recording cases in an external destination. Do not invent a campaign or log destination; retain unresolved cases for review without claiming they are resolved.
- Propose setting the company list's Outreach Status to `Researched` and recording the number of contacts found in Notes. Do not change the actual list without approval.
- Roxy's pre-submission checklist: 10 companies completed; 3–5 contacts each or documented small-company exception (flag any shortfall); no duplicates; current employment evidence for each person; no shared inboxes; exactly one permitted Email Confidence label; blank emails properly labeled. Bring missing evidence and exceptions to review; never fabricate completion.
- Roxy checks the completed batch first. Then Bridget marks each row ✅, ❌ or a note in the existing approval process, separately from the fixed prospecting columns. Complete and review this batch before starting another. Neither research completion nor a proposed `Researched` status is outreach authorization.

## Step 7a: Mandatory unresolved outreach checks
- Preserve Step 6 verification safeguards and Step 6a Dripify duplicate checks. Prospecting completion does not establish permission to contact.
- HubSpot suppression, historical hard bounces, opt-outs and applicable subscription restrictions, active sequences, prior outreach/replies and cross-channel duplicate/campaign conflicts must be treated as unresolved until supported evidence is available through a separate eligibility gate. The repository has offline prototypes (`scripts/eligibility.py` and `scripts/evidence.py`), not verified live provider collection or a send-time enforcement path. Do not assume an endpoint, property, subscription state or enrollment status exists or is accessible.
- Use the separate linked evidence record and existing offline checker for eligibility review; never bypass it for an anchor, source agreement, prior reply or unknown/catch-all/unverified result. Unsupported, missing, stale, incomplete or failed evidence stays pending; confirmed adverse evidence remains blocking. `eligible_for_review` is not authorization to send, and `authorized_to_send` remains false. Consult `scripts/ELIGIBILITY.md` and `scripts/HUBSPOT_VERIFICATION.md` for prototype limits and capability-verification requirements. Email-selected records remain pending for cross-channel checks until Roxy approves a resolution; do not silently run a prohibited Dripify lookup or switch channels to evade the pending state.
- In particular, Step 6a's email-selected path skips Dripify lookup; `not checked — email selected` is not proof of no active Dripify outreach. Flag the cross-channel check as unresolved rather than calling the contact safe to send.
- Missing information, provider failures, incomplete results, stale evidence and unresolved conflicts cannot qualify for outreach. An opt-out or restriction must not be bypassed by switching channels. All eligibility flags remain for review only; do not send, enroll or update external records through prospecting.

## Step 8: Load into HubSpot
- Only after Roxy's review, Bridget's approval and the required authorization for HubSpot changes; prospecting or a found email alone never authorizes loading contacts for outreach.
- Two lists: existing contacts and new contacts. Do not recreate an existing HubSpot contact. Keep existing-contact review separate from new-prospect handling; prior replies do not authorize new enrollment or repeat outreach.
- Associate approved contacts with the relevant campaign only after authorization. The prospecting workflow itself does not perform writes; unresolved eligibility or suppression checks cannot be bypassed by approval of research alone.

## Additional SOP merge: unresolved decisions for Roxy/Bridget
The additional reference is "Envision Contact Sourcing SOP (accuracy first, then speed)" supplied in this conversation. Complementary research and review instructions are merged above. Confirmed sourcing requirements: 3–5 qualified contacts per brand and senior-marketing-first priorities with social/influencer/community managers/coordinators as secondary options; those two conflicts are resolved and are no longer decisions pending. The remaining differences below are not approvals to change policy. Preserve the current established requirements and all safety controls until Roxy or Bridget confirms changes; document the resolution before changing instructions.

| Topic | Established requirement | Additional reference | Decision needed / current handling |
| --- | --- | --- | --- |
| Executive exceptions and exclusion details | Existing executive restrictions and role exclusions remain in force | VP Marketing if no social person; explicit exclusion of product | Confirm any additional executive exception and treatment of product-only roles. No new VP fallback or change to established exclusions is authorized by the priority decision. |
| Tiny-company fallback | One marketing person plus owner/founder as contact #2; other executive exceptions require confirmation | Tiny brand with no marketer: owner/founder marked "fallback" | Confirm whether the no-marketer fallback is an additional permitted exception and how to label it. Do not infer a new executive exception. |
| Sheet and labels | Exact 14-column sheet and five Email Confidence labels, with separate verification/eligibility evidence | Shorter review sheet with confirmed / pattern-confirmed / unverified status labels | Confirm whether a separate legacy status view is wanted. Keep the established sheet and five labels; do not add or map legacy labels as confidence or eligibility. Provider verification status remains separate. |
| Existing contacts and replies | Do not add existing contacts as new prospects; existing-contact history is reviewed separately | Prioritize prior repliers and use two existing/new lists | Prioritize existing repliers for review without recreating them. Confirm recontact/campaign handling and approval scope; a reply never waives suppression, duplicate or eligibility checks. |
| Email anchors and catch-all risk | Pattern-inferred provenance; positive verification, current employment evidence and no unresolved risks; second check cannot override unresolved risk | Source agreement means "confirmed"; matching anchor wins; "valid: use"; trust anchor on catch-all domains | Confirm intended discovery-only meaning. Keep strict verification/eligibility safeguards: anchors and agreement support sourcing only, invalid blocks email, and unknown/catch-all/unverified remains pending. |
| "Don't guess" and pattern generation | Evidence-backed pattern candidates allowed only as labeled hypotheses, never automatically confirmed | "Don't guess", alongside instructions to build addresses from patterns | Confirm whether further pattern-generation limits are intended. Never fabricate discovered evidence; retain current pattern-inferred safeguards and unresolved conflict handling. |
| Input list versus review sheet | Existing brand input list plus fixed prospecting review sheet | Start with Brand, Tier, Website; later use a different review-sheet layout | Keep Brand, Tier, Website as input fields only, not a replacement review schema. Confirm any desired separate presentation. |

Continue to ask Roxy about the unresolved-log/supporting-record location and unspecified executive exceptions. Freshness, recontact, subscription applicability, approval scope and email-selected cross-channel policy remain unresolved as documented in the eligibility references; this merge does not invent answers.

## Rules
- Never skip a step to go faster. If a step comes up empty, say so and keep searching.
- Apollo reveals are part of the process. Any other paid credits need Roxy's OK.
- No HubSpot changes without Roxy's OK.
