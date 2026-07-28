# Sleep Studio Business Rules

1. Access to a creator account is granted only after Google verifies the email or a valid one-time email link is consumed.
2. Sleep Studio never stores or accepts creator passwords.
3. Email sign-in links expire after 15 minutes.
4. Each email sign-in link can be used only once.
5. Sign-in requests return the same confirmation whether or not an account already exists.
6. An email address may request no more than five sign-in links per hour.
7. Google sign-in must return a verified email and a token issued for Sleep Studio's configured OAuth client.
8. One normalized email address represents one Sleep Studio user, even when multiple login methods are linked.
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
20. Free-plan usage is counted from automatic storytelling jobs created during the current UTC calendar month.
21. A free creator cannot create a story after reaching the administrator-assigned monthly job limit.
22. A story duration cannot exceed the creator's administrator-assigned maximum minutes per job.
23. A non-admin creator may have no more than one queued, processing, or publishing job at a time.
24. Administrators may set a creator's monthly limit from zero to 10,000 jobs.
25. Administrators may set a creator's maximum story duration from 0.5 to 600 minutes.
26. Administrators may activate or suspend creators but cannot modify another administrator through the customer-limit form.
27. Account-limit changes apply to new job requests and do not cancel work already processing.
28. Authentication, account-limit changes, job creation, YouTube publishing, sharing, retrying, and deletion require server-side validation; browser controls alone are never authoritative.
29. OAuth state, authenticated sessions, YouTube connections, and all state-changing forms must use anti-forgery protection appropriate to their flow.
30. Authentication errors must avoid revealing whether an email address is registered, and passwords, OAuth tokens, secrets, or one-time tokens must never be stored in plaintext.
