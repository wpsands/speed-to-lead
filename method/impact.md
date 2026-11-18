# Pipeline impact model

The rubric measures how fast and how well a company responds. This model estimates what a faster response is worth in pipeline. The code is `impact_model` in `scripts/stl.py`; this file and the code must agree.

## Inputs

| Input | Source | Flag |
|---|---|---|
| Current time to first human touch | Measured by the test. A company with no human touch is modeled at the full window length, and the report says so. | `--current-minutes` overrides it |
| Target time to first human touch | Chosen by the user. Default 1 hour. | `--target-minutes` |
| Demo requests per month | The company's CRM | `--monthly-requests` |
| Share of requests that become qualified meetings today | The company's CRM | `--qualify-rate` |
| Share of qualified meetings that become opportunities | The company's CRM | `--opp-rate` |
| Average pipeline per opportunity | The company's CRM | `--pipeline-per-opp` |
| Win rate (optional) | The company's CRM. Adds a bookings column. | `--win-rate` |
| Qualification rate for leads contacted within the target time (optional) | The company's CRM | `--qualify-rate-at-target` |
| Cap on the qualification rate | Default 90% | `--cap` |

Rates accept `0.2`, `20` or `20%`.

## Calculation

Baseline per month: requests × qualify rate = qualified meetings; × opp rate = opportunities; × pipeline per opportunity = pipeline.

Only the qualification rate changes. Every later step keeps the company's own rates, so the model never assumes faster response also lifts win rate or deal size.

**With the company's own data.** When `--qualify-rate-at-target` is given, the new qualification rate is that number, and it is the only scenario shown. This is still a correlation in the company's history: leads contacted fast may also have been better leads. Treat it as an upper bound.

**Without it.** Three scenarios raise the qualification rate at full effect:

| Scenario | Uplift at full effect |
|---|---|
| Conservative | +25% |
| Base | +50% |
| Aggressive | +100% |

Full effect applies when the current response time is 24 or more times the target, for example 24 hours or more against a 1-hour target. For smaller gaps the uplift is scaled on a log scale: share = log(current ÷ target) ÷ log(24). Moving from 2 hours to 1 hour gets about 22% of the effect. A company already at or below the target gains nothing.

New qualification rate = current rate × (1 + uplift × share), capped at `--cap`. A rate already above the cap is never lowered.

Outputs: extra qualified meetings and opportunities per month, and extra pipeline per month, quarter and year. With a win rate, extra bookings per year.

## Why the scenarios are small

The research effect is much larger than any scenario here.

- Firms that tried to contact a lead within an hour were nearly seven times as likely to qualify it as firms that tried an hour later, and more than 60 times as likely as firms that waited 24 hours or longer (Oldroyd, McElheran and Elkington, Harvard Business Review, 2011).
- A study of about 15,000 leads found the odds of qualifying a lead were 21 times higher when contact was attempted within 5 minutes than at 30 minutes (Lead Response Management Study, MIT and InsideSales.com, 2007). It was sponsored by a sales software vendor.

Three reasons not to apply those numbers directly:

1. They are odds ratios. At a 20% qualification rate, a 60× odds ratio implies about 94%. Rates and odds diverge as rates rise.
2. They are observational. Firms that respond fast may differ in other ways: better lead sources, better reps, better routing.
3. They are not recent and include consumer-facing companies.

The scenarios use the research for the direction and the shape of the effect, with steep losses over the first hours and a full loss by about a day. They do not use it for size. Replace them with the company's own data whenever possible.

## How to get the company's own number

Export leads from inbound demo requests for the last two to four quarters with three fields: created time, first activity time by a person, and whether the lead reached the qualified stage. Group by time to first activity (under 1 hour, 1 to 24 hours, over 24 hours) and compare qualification rates. Put leads that never got a human touch in the over-24-hours group. Do not drop them.

## Sources

- Oldroyd, J. B., McElheran, K. and Elkington, D. "The Short Life of Online Sales Leads." Harvard Business Review, March 2011. https://hbr.org/2011/03/the-short-life-of-online-sales-leads
- Oldroyd, J. B. Lead Response Management Study, MIT and InsideSales.com, 2007. https://www.mortech.com/hs-fs/hub/25649/file-13535879-pdf/docs/mit_study.pdf
