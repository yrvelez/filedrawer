# Pre-analysis plan: Economic-contribution framing and immigration attitudes (SYNTHETIC DEMO)

This is a synthetic pre-analysis plan written to exercise the filedrawer pipeline. The
"study" it describes was never fielded; the accompanying data are simulated.

## Design

Online survey experiment, U.S. adults from an opt-in panel, target N = 400. After
consent and demographics, respondents are randomly assigned with equal probability to
read either (a) a paragraph describing immigrants' economic contributions
(**treatment**) or (b) a paragraph with neutral population facts (**control**).
Assignment is recorded in the embedded data field `condition`.

## Outcomes

Primary outcome: a pro-immigration attitude index, the mean of three 7-point agree-disagree
items (immigrants strengthen the economy; immigration levels should be reduced, reverse
coded; path to citizenship), scaled 1-7 with higher values more pro-immigration.

Secondary outcome: policy attitudes toward immigration (7-point favor-oppose).

## Hypotheses

- **H1.** Respondents in the treatment condition will report more pro-immigration
  attitudes on the primary index than respondents in the control condition.
- **H2.** Respondents in the treatment condition will report more favorable policy
  attitudes than respondents in the control condition, adjusting for party identification
  and age in years.

## Estimation

OLS regression of each outcome on a treatment indicator with HC2 robust standard errors,
two-sided tests, alpha = 0.05. H2 adds party identification (indicators) and age in years
as covariates.

## Heterogeneity (registered)

- **S1.** The H1 treatment effect will be larger among Democrats than among Republicans.
  Tested with a treatment x party interaction (Democrat vs Republican; Independents and
  others excluded from this test).

## Exclusions

Respondents who do not complete the survey are excluded. Attention-check failures are
retained in the main analysis and used only in a robustness check.

## Multiple testing

No correction; H1 is the single primary test.
