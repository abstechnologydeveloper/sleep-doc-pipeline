# My Automation Studio cost model

The published plan prices are starting prices, not proven margins. Review them monthly using
real invoices and completed-job measurements.

For each plan, calculate the worst reasonable monthly variable cost as:

`stories × (script cost + narration cost + image allowance cost + render/VPS cost + storage and delivery cost) + Paystack fee + support allowance`

Then calculate contribution margin as:

`(plan price - variable cost) / plan price`

Track actual cost per completed minute, cost per generated image, failed-provider spend,
average stored gigabytes, R2 delivery, and support time. Do not treat advertised provider
prices as the final cost when exchange rates, taxes, retries, failed generations and payment
fees are excluded. Change plan limits or prices before selling a tier whose measured margin
cannot cover operations and support.
