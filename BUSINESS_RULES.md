# My Automation Studio Business Rules

1. Access to a creator account is granted only after Google verifies the email or a valid one-time email link is consumed.
2. My Automation Studio never stores or accepts creator passwords.
3. Email sign-in links expire after 15 minutes.
4. Each email sign-in link can be used only once.
5. Sign-in requests return the same confirmation whether or not an account already exists.
6. An email address may request no more than five sign-in links per hour.
7. Google sign-in must return a verified email and a token issued for My Automation Studio's configured OAuth client.
8. One normalized email address represents one My Automation Studio user, even when multiple login methods are linked.
9. The configured `ADMIN_EMAIL` becomes an administrator after its email is verified.
10. Administrators may onboard creators by email, and all new non-admin accounts start as active creators on the free plan.
11. A suspended creator cannot access an authenticated workspace or create work.
12. Creators can view only jobs and media they own.
13. Administrators can view all customers, jobs, and media, including unowned legacy work, but cannot generate content or connect creator channels.
14. Every new generated, uploaded, or social-post job must record its creator owner, and its durable media must use that creator's private R2 key prefix.
15. A creator cannot view, stream, download, publish, retry, share, revoke, cancel, or delete another creator's job.
16. Public video access requires an unguessable active share token and does not expose the creator workspace.
17. Revoking a public link makes that link unusable immediately.
18. Deleting a job invalidates its public link and removes unreferenced R2 media without deleting media reused by another job.
19. Public links can be created only for finished videos with an available local or R2 media reference.
20. Plan usage is counted from automatic storytelling jobs created during the creator's rolling previous 30 days, preventing calendar-boundary quota doubling.
21. A creator cannot create a story after reaching the account's effective monthly job limit.
22. A story duration cannot exceed the creator's administrator-assigned maximum minutes per job.
23. A non-admin creator may have no more than one queued, processing, or publishing job at a time.
24. Administrators may set a creator's monthly limit from zero to 10,000 jobs.
25. Administrators may set a creator's maximum story duration from 0.5 to 600 minutes.
26. Administrators may activate or suspend creators but cannot modify another administrator through the customer-limit form.
27. Account-limit changes apply to new job requests and do not cancel work already processing.
28. Authentication, account-limit changes, job creation, YouTube publishing, sharing, retrying, and deletion require server-side validation; browser controls alone are never authoritative.
29. OAuth state, authenticated sessions, YouTube connections, and all state-changing forms must use anti-forgery protection appropriate to their flow.
30. Authentication errors must avoid revealing whether an email address is registered, and passwords, OAuth tokens, secrets, or one-time tokens must never be stored in plaintext.
31. My Automation Studio offers Free, Basic, Pro, and Studio plans; only Basic, Pro, and Studio require payment.
32. The Free plan includes 3 stories per rolling 30 days, up to 5 minutes and 8 generated scene images per story.
33. The Basic plan is priced at ₦15,000 per 30 days and includes 10 stories, up to 10 minutes and 16 generated scene images per story.
34. The Pro plan is priced at ₦40,000 per 30 days and includes 30 stories, up to 20 minutes and 32 generated scene images per story.
35. The Studio plan is priced at ₦100,000 per 30 days and includes 100 stories, up to 30 minutes and 48 generated scene images per story.
36. Published naira prices are commercial starting points and must be reviewed against actual provider bills, Paystack fees, exchange-rate exposure, support costs, and observed usage.
37. A job snapshots its narration voice and image allowance when it is created, so later account changes do not alter retries already in progress.
38. Storyboards must preserve every meaningful narrative beat while consolidating or reusing visuals to remain within the job's paid-image allowance.
39. An image allowance limits distinct paid scene generations, not the number of timed scenes, transitions, or camera movements in the finished video.
40. Creators may select only a server-approved AI33.Pro narration voice; arbitrary model or voice identifiers are rejected.
41. Voice labels state the narrator and expected delivery, but creators must approve the generated narration before video assembly.
41b. Finished narration is transcribed for speech-measured captions; if transcription is unavailable, saved narration timing remains the non-blocking fallback.
41c. Background music must be instrumental, story-specific, manually reviewable, mixed quietly beneath narration, and non-blocking if the music provider fails.
41d. Scene and thumbnail images use AI33.Pro Seedream first, then Cloudflare Flux and Leonardo fallbacks; a provider failure must not discard already completed images.
41e. A thumbnail uses a shortened real video title plus one large story character or historically accurate participant; it must remain truthful, uncluttered, high-contrast and readable on a phone.
41f. Story images remain completely still; videos use only soft fades between images, with no zoom, pan, shake, slide, moving particles or animated weather overlays.
41g. Narration keeps a natural speaking pace, uses longer sentence-safe requests, and removes only excessive silence at generated-audio joins.
41h. Invention, origin, archaeology and “how did humans” topics require grounded research; unknown inventors remain unknown, and fictional named creators or origin events are forbidden.
41i. The complete creator request determines whether a video is factual or fictional; uncertain topics receive semantic classification before writing, and factual mode is not selected from one title keyword alone.
41j. For every factual subject, verification covers dates, measurements, materials, archaeological interpretation, cultural attribution, intended purpose, chronology, military use, safety, effectiveness, and present-day claims; unsupported absolutes and broad historical conclusions must be removed or qualified.
41k. Factual research may use primary documents, archaeological publications, museums, governments, universities, peer-reviewed work, and established reference institutions; crowd-written sites, forums, videos, blogs, tourism, retail, and unsourced search summaries cannot establish a factual claim.
41l. When factual correction makes narration shorter than its approved duration, one length-repair pass may add only context already supported by the same research; the repaired narration must pass the factual audit again before approval.
41m. A factual audit passes only when every material narration claim is individually marked as supported and mapped to reliable evidence; a general bibliography or bare verdict is insufficient.
41n. When a speech transcript is available, image boundaries and captions share one transcript-aligned timeline; captions retain the approved script wording, and estimated chunk timing is used only as a fallback.
41o. Paid story images are distributed across the full narration; each image must match the first spoken moment of its scene, and reuse is allowed only for an adjacent passage with the same subject, place, period, and visible action.
41p. Approved narration outranks generic visual defaults for image content, while safety, native 16:9 output, and the selected illustrated medium remain fixed.
41a. Every generated scene image and thumbnail uses a coherent animated storybook/cartoon medium; human characters must remain visibly illustrated and must never become photographs or live-action people.
42. Paystack Checkout is the only browser-accessible path for purchasing paid access, and plan entitlements are never accepted from browser form or callback values.
43. A paid plan becomes effective only after My Automation Studio verifies a successful Paystack transaction or accepts a correctly signed `charge.success` webhook.
44. Invalid or unsigned Paystack webhooks must be rejected before any account or payment data changes.
45. Every Paystack payment uses a unique stored reference and is activated idempotently so callbacks and webhook retries cannot grant access twice.
46. Expired, failed, incomplete, mismatched, or otherwise non-entitled payment access returns the creator to Free limits without deleting completed media.
47. Subscription downgrades affect new jobs; queued or processing jobs keep the limits captured when they were created.
48. Paystack-funded access lasts 30 days, is renewed manually, and does not create an automatic recurring charge in My Automation Studio.
49. Administrators may review and override creator plan labels and operational limits, but verified Paystack transactions remain the source of truth for payment status.
50. Provider credentials, Paystack secrets, OAuth secrets, and encryption keys must come from protected environment configuration and must never be exposed in templates, logs, public links, or client-side code.
51. Every creator has a private sequential job number that begins at 1 and increases atomically across generated stories, uploaded media, and social-post jobs.
52. Creator-facing pages display the creator-local job number, while internal routes and relationships continue using the globally unique database job ID.
53. Deleting a job never reuses or renumbers its creator-local job number, preserving stable references in support conversations and audit history.
54. Existing jobs receive creator-local numbers in original creation order during the one-time numbering migration.
55. A creator profile may store a channel name, narration voice, voice delivery, and default duration; it must not apply a global story recipe to new videos.
56. Creator profile defaults are private workspace settings and must not appear on public share pages unless deliberately included in published metadata.
57. Profile fields have server-enforced limits, and narration voice identifiers must come from the approved server-side catalog.
58. Story idea starters remain editable suggestions and must not silently add a niche, audience, visual style, or goal to a video.
59. Prompt guidance must encourage one clear situation, simple language, an honest curiosity gap, and an ending that answers the story’s central question.
60. Suggested prompts must remain original starting points and must never request imitation of a named creator, artist, studio, franchise, or copyrighted character.
61. Creator guidance may improve focus and production consistency but must never promise views, subscribers, revenue, virality, or platform approval.
62. Landing-page success content must be labeled honestly as an illustrative workflow unless it is supported by a verified customer, permission, and evidence.
63. The public landing page defaults to dark mode, while authenticated creators retain their saved theme preference.
64. A user may switch to dark mode, and that preference is stored only in the user’s browser without changing account or job data.
65. All essential navigation, forms, statuses, media controls, and billing actions must remain readable and usable in both light and dark themes.
66. The public landing page must provide Open Graph and large-image card metadata with absolute HTTPS URLs when `PUBLIC_BASE_URL` is configured.
67. The landing social-preview image must be a directly accessible 1200×630 raster image with a stable content type and cache policy suitable for WhatsApp and other crawlers.
68. Public job shares must continue to use the finished job thumbnail as their preview whenever that thumbnail is available.
69. Social-preview metadata must describe the product or shared work accurately and must not contain hidden claims, misleading engagement bait, or private creator information.
70. Theme, profile guidance, social previews, and creator-local job numbering must degrade safely: missing optional profile data shows general guidance, while security and ownership checks remain unchanged.
71. Fiction must have one identifiable central character with a simple desire, need, duty, or question; factual work instead follows one clear evidence-led subject or question.
72. The opening must establish an understandable situation and curiosity hook early without misleading clickbait, a long atmospheric preamble, or unexplained lore.
73. Events must follow a cause-and-effect chain; major discoveries, decisions, and consequences cannot appear only because the script needs to move forward.
74. The central problem must become clear before the middle of the story and must remain understandable when heard once without visual support.
75. The middle must introduce meaningful progress, a complication, discovery, reversal, relationship change, or choice instead of repeating atmosphere and near-identical events.
76. The story must escalate gently through emotional importance or curiosity rather than relying on louder danger, graphic detail, or sudden shock.
77. Recurring characters must keep stable names, ages, relationships, knowledge, motivations, physical traits, clothing, and speaking styles unless the narrative explicitly changes them.
78. Locations, props, time of day, weather, distances, and travel must remain spatially and chronologically consistent across narration and visuals.
79. Dialogue must sound distinct enough to identify speakers, remain easy to follow aloud, and always reveal character, advance action, or provide necessary relief.
80. Humor must arise naturally from character or situation and must not weaken grief, suspense, cultural context, historical dignity, or the intended sleep tone.
81. Exposition must be delivered through concrete action, observation, short dialogue, or necessary narration instead of lectures, encyclopedic paragraphs, or repeated explanation.
82. Every important setup, promise, clue, object, rule, and relationship introduced as significant must receive a payoff, deliberate reframing, or clear acknowledgement before the ending.
83. A fictional climax must result from the central character’s accumulated choices, learning, courage, kindness, restraint, or problem-solving rather than coincidence or an unexplained rescue; factual work must not manufacture a climax.
84. A fictional ending must resolve the central dramatic question and show the emotional change; a factual ending must answer the opening question only as far as evidence allows. Neither may rush, moralize, recap the whole story, or open a new major conflict.
85. Children’s stories must use age-appropriate vocabulary, stakes, humor, runtime, and safety while still respecting the audience’s intelligence and avoiding babyish repetition.
86. Adult stories must remain plain and accessible without becoming childish, overly solemn, academic, vague, or filled with decorative “big grammar.”
87. Historical, cultural, scientific, documentary, and folklore stories must distinguish established fact, respectful adaptation, and original invention; uncertain claims cannot be presented as verified truth.
88. Story scene planning must give every meaningful action, reveal, location change, emotional turn, and payoff a matching visual beat while reusing visuals only when continuity honestly permits it.
89. Titles, descriptions, thumbnails, opening hooks, narration, and endings must promise and deliver the same central story rather than using unrelated curiosity bait.
90. Before narration generation is accepted, its prompt must demand originality, audience fit, continuity, and a complete resolution suited to its mode: character-driven cause and effect for fiction, or evidence, chronology, uncertainty, and documented consequences for factual work.
91. Story usage is stored independently from job records so deleting completed work never restores paid allowance.
92. Failed or cancelled story generation releases its allowance; retrying it reserves allowance again and must respect the active-job and plan limits.
93. Creator-supplied concepts and metadata are checked server-side for prohibited content before work or storage begins.
94. A disputed Paystack charge pauses only the entitlement funded by that active payment and never changes another customer's access.
95. An active paid creator cannot accidentally downgrade early; a lower tier may be selected after the current access period ends.
96. Creators can review their own payment-attempt history, while administrators can review payment activity across customers.
97. Job completion, job failure, payment confirmation, payment disputes and approaching plan expiry create private creator notifications.
98. Failed-job notifications explain that allowance was restored without exposing internal provider credentials or stack traces.
99. Administrators can search customers by name or email and every administrator limit change creates an audit event.
100. Creator uploads must use an approved video type and stay below the server-configured maximum upload size.
101. Account exports contain creator account, job metadata and payment records but never authentication tokens or decrypted social credentials.
102. Account deletion requires the authenticated creator to enter the account email and removes database ownership records and associated private media.
103. Privacy, terms, acceptable-use, copyright, billing and support information must remain publicly accessible before sign-in.
104. Legal policy drafts require review by qualified counsel before public commercial launch.
105. Narration quality checks remove chapter-style artefacts and duplicate consecutive passages and reject an incomplete word count.
106. Completed jobs remain chargeable even after their share link or job record is removed.
107. No automated recurring Paystack charge is created; creators deliberately renew access through checkout.
108. Plan profitability must be reviewed with actual provider invoices, exchange rates, failure spend, storage, payment fees and support cost.
109. The application does not implement an automated refund workflow; billing errors are handled through the documented support channel and Paystack administration.
110. Facebook, Instagram and TikTok publishing remain outside this release; their presence in a platform list never represents an active connector.
