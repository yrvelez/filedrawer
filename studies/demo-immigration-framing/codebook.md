# Codebook: Immigration Framing Experiment (SYNTHETIC DEMO)

Source: qsf

**Arms** (`condition`, source: flow_embedded): control, treatment

| column | qid | type | label | values |
|---|---|---|---|---|
| `StartDate` |  | system | StartDate |  |
| `EndDate` |  | system | EndDate |  |
| `Status` |  | system | Status |  |
| `IPAddress` |  | system | IPAddress |  |
| `Progress` |  | system | Progress |  |
| `Duration (in seconds)` |  | system | Duration (in seconds) |  |
| `Finished` |  | system | Finished |  |
| `RecordedDate` |  | system | RecordedDate |  |
| `ResponseId` |  | system | ResponseId |  |
| `RecipientLastName` |  | system | RecipientLastName |  |
| `RecipientFirstName` |  | system | RecipientFirstName |  |
| `RecipientEmail` |  | system | RecipientEmail |  |
| `ExternalReference` |  | system | ExternalReference |  |
| `LocationLatitude` |  | system | LocationLatitude |  |
| `LocationLongitude` |  | system | LocationLongitude |  |
| `DistributionChannel` |  | system | DistributionChannel |  |
| `UserLanguage` |  | system | UserLanguage |  |
| `consent` | QID1 | question | Do you consent to participate in this study? | 1=Yes, I consent; 2=No, I do not consent |
| `pid3` | QID2 | question | Generally speaking, do you usually think of yourself as a Democrat, a Republican | 1=Democrat; 2=Republican; 3=Independent; 4=Something else |
| `ideo5` | QID3 | question | In general, how would you describe your political views? | 1=Very liberal; 2=Liberal; 3=Moderate; 4=Conservative; 5=Very conservative |
| `agecat` | QID4 | question | What is your age? | 1=18-29; 2=30-44; 3=45-64; 4=65 or older |
| `gender` | QID5 | question | How do you describe yourself? | 1=Man; 2=Woman; 3=Non-binary or another description |
| `educ` | QID6 | question | What is the highest level of education you have completed? | 1=High school or less; 2=Some college; 3=Bachelor's degree; 4=Graduate degree |
| `imm_att_1` | QID9 | question | How much do you agree or disagree with the following statements? — Immigrants st | 1=Strongly disagree; 2=Disagree; 3=Somewhat disagree; 4=Neither agree nor disagree; 5=Somewhat agree; 6=Agree; 7=Strongly agree |
| `imm_att_2` | QID9 | question | How much do you agree or disagree with the following statements? — Immigration l | 1=Strongly disagree; 2=Disagree; 3=Somewhat disagree; 4=Neither agree nor disagree; 5=Somewhat agree; 6=Agree; 7=Strongly agree |
| `imm_att_3` | QID9 | question | How much do you agree or disagree with the following statements? — Undocumented  | 1=Strongly disagree; 2=Disagree; 3=Somewhat disagree; 4=Neither agree nor disagree; 5=Somewhat agree; 6=Agree; 7=Strongly agree |
| `policy_support` | QID12 | question | Overall, do you favor or oppose increasing the number of legal immigrants admitt | 1=Strongly oppose; 2=Oppose; 3=Somewhat oppose; 4=Neither favor nor oppose; 5=Somewhat favor; 6=Favor; 7=Strongly favor |
| `attn` | QID10 | question | To check that you are reading carefully, please select 'Somewhat agree' below. | 1=Strongly disagree; 2=Disagree; 3=Somewhat disagree; 4=Neither agree nor disagree; 5=Somewhat agree; 6=Agree; 7=Strongly agree |
| `open_comment` | QID11 | question | Is there anything else you would like to tell us about your views on immigration |  |
| `condition` |  | embedded | condition |  |
