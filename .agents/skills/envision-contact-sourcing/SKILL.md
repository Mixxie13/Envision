---
name: envision-contact-sourcing
description: Find, verify and cross-check marketing contacts and emails for a list of Envision brand partners. Use when asked to "find contacts for these companies/brands", source social/marketing contacts, or build an outreach contact list for an Envision campaign. Accuracy first, then speed.
---

# Envision Contact Sourcing (accuracy first, then speed)

Input: a list of brands (Brand, Tier, Website). Output: a pre-filled review sheet, one row per contact. Nothing goes into HubSpot until approved.

## Step 1: Batch planning, ownership and HubSpot checks
- Work in batches of 10 companies in the existing company-list priority order. Complete and review one batch before starting another; do not invent a new priority order. Flag a final partial batch for Roxy's direction rather than inventing companies.
- Before researching contacts, confirm brand ownership, parent/operator and actual employee email domain using evidence. A website domain alone does not establish the employee email domain, especially after an acquisition. Record evidence, conflicts and routing decisions in Ownership / Routing Notes; leave unresolved facts explicitly pending.
- Check HubSpot first by company name and website domain and inspect associated contacts. Match identities and known email candidates; do not add existing contacts as new prospects. Flag ambiguous matches rather than assuming absence.
- Flag anyone who has replied before for review and preserve their sourcing priority; prior replies do not authorize repeat outreach or bypass suppression checks.
- These are research instructions, not evidence that a HubSpot integration or property exists. If access or required information is unavailable, log the unresolved check; do not claim it passed.

## Step 2: Find relevant current contacts
- Find 3–5 current relevant people per company using the role order below. For a small company with only one marketing person, include that person and the owner/founder as contact #2, with evidence and the exception noted. Never invent people or include irrelevant roles to fill a quota; log genuine shortfalls.
- Prioritize roles in this exact order:
  1. Brand Director
  2. Head of Brand / Brand Lead
  3. Senior Brand Manager / Brand Manager
  4. Marketing Director
  5. Marketing Manager or higher
  6. Social Media Lead / Manager
  7. Influencer Marketing Lead / Manager
  8. Community Lead / Manager
  9. Marketing Partnerships / Brand Partnerships
  10. Growth / Digital / Ecommerce leadership
- Founder, CMO or Chief Brand Officer may be included only under Roxy's stated exceptions. The small-company owner/founder exception above is explicit; other exception criteria are not supplied here. Ask Roxy to confirm them before including other executive exceptions; do not infer exceptions from seniority or a contact shortfall.
- Exclude sales-only, finance, customer support, engineering/IT/data, general management, operations, HR, design-only, unrelated partnerships and executives outside the confirmed exceptions.
- Research the confirmed parent/operator where appropriate and document the routing. For every candidate, capture full name, current role, function, personal LinkedIn profile URL and role evidence. Do not guess.

## Step 3: Check each name on LinkedIn
- Confirm current employment, role and company using each person's own LinkedIn profile. Record the personal profile URL in LinkedIn and the role evidence URL in Role Source URL, with the research date.
- Still in a relevant current role: keep. Left or changed to an irrelevant role: remove from the candidate shortlist, log `Departed-Role Changed`, and research the current role holder.
- If Apollo and LinkedIn disagree, use LinkedIn as the primary employment source. Flag unresolved identity or employment ambiguity in the unresolved log; do not treat it as confirmed.

## Step 4: Apollo search and reveal
- Search Apollo with the same roles and exclusions.
- Check any new names on LinkedIn (as in Step 3).
- Reveal emails only for people who pass the LinkedIn check.

## Step 5: Cross-reference the names
- Found by both AI and Apollo, confirmed on LinkedIn: strongest.
- Found by only one source: keep only if LinkedIn confirms.
- Sources name different people for the role: use each person's LinkedIn profile as primary evidence; log unresolved ambiguity.
- Rank 3–5 relevant current contacts per company using Step 2's role order, preserving the documented small-company exception and honest shortfalls. Check for duplicates within the batch, earlier batches and existing HubSpot contacts.

## Step 6: Find and cross-check the emails
Run the helper commands below from the repository root, subject to the existing paid-credit approval rules. Codex helpers use only the configured `RELAY_TOKEN` through `https://envision-email-relay.onrender.com`; never print its value. Provider keys belong on the relay, not in Codex. Read the provider result in the returned JSON; `ok: true` alone does not establish email validity.
Use only individual professional email candidates. Never use shared inboxes (including info@, hello@, press@ or team/department addresses). Leave Professional Email Candidate blank when no personal email is found; never insert a placeholder or a shared inbox.

1. **HubSpot:** if they replied before, prioritize that email as a candidate; prior replies do not bypass the eligibility checks below.
2. **Apollo:** the email revealed in Step 4.
3. **AI anchor search, once per company:**
   - Find one real, published email of a named staff member. Generic inboxes (info@, hello@, press@) don't count.
   - Look in press releases, recall notices, trademark filings, team pages, German Impressum pages, LinkedIn posts.
   - Record the email domain (often different from the website), the pattern (first.last@, flast@, first@) and the source link.
   - Run at least 10 searches before calling it "not found".
   - Build each person's email from that pattern and label its source `pattern-inferred`, never automatically confirmed. Keep this source label even if later verification makes the address eligible.
4. **Findymail:** search by name and company domain.
   - Run `python3 scripts/findymail.py find --name "<full name>" --domain "<company domain>"`.
5. **Compare:**
   - All agree: record source agreement; agreement alone does not confirm deliverability or outreach eligibility.
   - Disagree: prioritize the email matching the anchor for verification, but retain unresolved conflicts as risk flags. No anchor: flag it, don't guess.
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
- Keep a separate linked verification/eligibility record keyed by Batch, Company, Full Name and LinkedIn (include the exact email under review). Preserve address source, verification status (positive / invalid / unknown / catch-all / failed / unverified), verification dates, provider names and exact results from every check, evidence, source conflicts, role/company confirmation evidence and dates, unresolved risks and resolution evidence, route, Dripify lookup results/dates and eligibility reasons. Keep `pattern-inferred` provenance separate from the exact Email Confidence labels. Ask Roxy where to keep this supporting record if no location is established.
- No Email Confidence label, discovered address or pattern alone authorizes outreach. Publicly confirmed addresses must also be re-verified before sending under Step 6; stale/missing verification or employment evidence remains unresolved. No automatic freshness period is defined here.
- Maintain an unresolved log with: Campaign, Batch, Company, Full Name, Role, LinkedIn URL, Reason unresolved, Date, Case Type (where applicable). Case Type values: Identity Ambiguous, Data Corruption, Departed-Role Changed, Other. Ask Roxy to confirm the log location before recording cases in an external destination. Do not invent a campaign or log destination; retain unresolved cases for review without claiming they are resolved.
- Propose setting the company list's Outreach Status to `Researched` and recording the number of contacts found in Notes. Do not change the actual list without approval.
- Roxy's pre-submission checklist: 10 companies completed; 3–5 contacts each or documented small-company exception (flag any shortfall); no duplicates; current employment evidence for each person; no shared inboxes; exactly one permitted Email Confidence label; blank emails properly labeled. Bring missing evidence and exceptions to review; never fabricate completion.
- Roxy checks the completed batch first. Then Bridget marks each row ✅, ❌ or a note in the existing approval process, separately from the fixed prospecting columns. Complete and review this batch before starting another. Neither research completion nor a proposed `Researched` status is outreach authorization.

## Step 7a: Mandatory unresolved outreach checks
- Preserve Step 6 verification safeguards and Step 6a Dripify duplicate checks. Prospecting completion does not establish permission to contact.
- HubSpot suppression, historical hard bounces, opt-outs and applicable subscription restrictions, active sequences, prior outreach/replies and cross-channel duplicate/campaign conflicts must be treated as unresolved until supported evidence is available through a separate eligibility gate. The repository does not currently enforce that gate; do not assume an endpoint, property, subscription state or enrollment status exists or is accessible.
- In particular, Step 6a's email-selected path skips Dripify lookup; `not checked — email selected` is not proof of no active Dripify outreach. Flag the cross-channel check as unresolved rather than calling the contact safe to send.
- Missing information, provider failures, incomplete results, stale evidence and unresolved conflicts cannot qualify for outreach. An opt-out or restriction must not be bypassed by switching channels. All eligibility flags remain for review only; do not send, enroll or update external records through prospecting.

## Step 8: Load into HubSpot
- Only after approval.
- Two lists: existing contacts and new contacts.
- Tag every contact with the campaign.

## Rules
- Never skip a step to go faster. If a step comes up empty, say so and keep searching.
- Apollo reveals are part of the process. Any other paid credits need Roxy's OK.
- No HubSpot changes without Roxy's OK.
