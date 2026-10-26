---
name: envision-contact-sourcing
description: Find, verify and cross-check marketing contacts and emails for a list of Envision brand partners. Use when asked to "find contacts for these companies/brands", source social/marketing contacts, or build an outreach contact list for an Envision campaign. Accuracy first, then speed.
---

# Envision Contact Sourcing (accuracy first, then speed)

Input: a list of brands (Brand, Tier, Website). Output: a pre-filled review sheet, one row per contact. Nothing goes into HubSpot until approved.

## Step 1: Set up the list and check HubSpot
- Put the brands in a sheet: Brand, Tier, Website.
- Pull each brand's existing HubSpot contacts.
- Flag anyone who has replied to us before. They go to the top.

## Step 2: AI finds the contacts
Run this prompt once per brand:

> Find up to 3 current marketing contacts at **{Brand}** ({website}). Target, in priority order:
> 1. Social media coordinators / associates
> 2. Social media, influencer or community managers
> 3. Brand or marketing managers
> 4. VP Marketing, only if no social person exists
>
> **Exclude:** sales, finance, operations, HR, customer service, product and design-only roles.
> If marketing sits with a parent company, search the parent (e.g. Playtex Baby → Angelcare, Orangetheory → Purpose Brands). For tiny brands with no marketer, give the founder or owner, marked "fallback".
> For each person, return name, title, LinkedIn URL and the source link. Don't guess.

## Step 3: Check each name on LinkedIn
- Still in that role: keep.
- Left or changed roles: drop them, and find who holds the role now.

## Step 4: Apollo search and reveal
- Search Apollo with the same roles and exclusions.
- Check any new names on LinkedIn (as in Step 3).
- Reveal emails only for people who pass the LinkedIn check.

## Step 5: Cross-reference the names
- Found by both AI and Apollo, confirmed on LinkedIn: strongest.
- Found by only one source: keep only if LinkedIn confirms.
- Sources name different people for the role: LinkedIn decides.
- Rank and keep up to 3 per brand.

## Step 6: Find and cross-check the emails
1. **HubSpot:** if they replied before, use that email.
2. **Apollo:** the email revealed in Step 4.
3. **AI anchor search, once per company:**
   - Find one real, published email of a named staff member. Generic inboxes (info@, hello@, press@) don't count.
   - Look in press releases, recall notices, trademark filings, team pages, German Impressum pages, LinkedIn posts.
   - Record the email domain (often different from the website), the pattern (first.last@, flast@, first@) and the source link.
   - Run at least 10 searches before calling it "not found".
   - Build each person's email from that pattern.
4. **Findymail:** search by name and company domain.
5. **Compare:**
   - All agree: confirmed.
   - Disagree: the email matching the anchor wins. No anchor: flag it, don't guess.
   - Only Apollo has one: label "unverified".
6. **NeverBounce:**
   - Valid: use. Invalid: drop.
   - Unknown or catch-all: second check with Findymail's verifier.
   - On catch-all domains "valid" proves nothing; trust the anchor.

## Step 7: Pre-filled review sheet
- One row per contact: brand, name, title, LinkedIn, email, status (confirmed / pattern-confirmed / unverified), anchor link, sources that agreed, date checked.
- Roxy checks it first. Then Bridget marks each row ✅, ❌ or a note.

## Step 8: Load into HubSpot
- Only after approval.
- Two lists: existing contacts and new contacts.
- Tag every contact with the campaign.

## Rules
- Never skip a step to go faster. If a step comes up empty, say so and keep searching.
- Apollo reveals are part of the process. Any other paid credits need Roxy's OK.
- No HubSpot changes without Roxy's OK.
