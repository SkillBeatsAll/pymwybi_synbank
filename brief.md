# **Syn Bank Share of Wallet Intelligence Challenge**

In partnership with Standard Bank Corporate & Investment Banking, this year’s hackathon invites students to tackle a commercially relevant data science challenge grounded in the realities of corporate banking. Teams will work with synthetic internal banking data and external public information to estimate how much of a corporate client’s total banking spend is captured by Syn Bank, a fictional corporate and investment bank created specifically for this event.

Corporate clients rarely use a single bank for every service they need. They spread their activity across multiple providers for reasons that include risk management, pricing tension, product specialisation, and geographic coverage, which makes share of wallet a critical commercial question for any coverage team. The challenge therefore asks teams to build an intelligence engine that estimates total wallet, quantifies Syn Bank’s current share, identifies the biggest commercial gaps, and shows how Generative AI can make those insights easier to act on Challenge Question.

Can you build a Share of Wallet Intelligence Engine for Syn Bank that estimates each client’s total banking wallet, quantifies Syn Bank’s current share across core product pillars, and identifies the highest-priority growth opportunities using data science and Generative AI? 

## **Bonus focus areas**

Teams are also encouraged to think about two additional dimensions of the problem:

- How an agent-based approach could forecast client cash cycles, payment timing, and the best moments for client engagement.

- How latency introduced by complex agent hierarchies, multiple data sources, and orchestration layers could be reduced through better solution design.

# **Business Context** 

Syn Bank is a fictional South African corporate and investment bank with a portfolio of 20 JSE-listed corporate clients across sectors such as mining, retail, manufacturing, financial services, consumer goods, and infrastructure. It offers services across Transactional Banking, Global Markets, and Investment Banking, but it is not the sole banking partner for any client in the portfolio.

This means that the real commercial opportunity is not simply to measure activity flowing through Syn Bank, but to estimate the total addressable wallet and highlight where competitors are capturing business instead. Teams should think like both data scientists and commercial bankers: the strongest solutions will translate data signals into practical recommendations that could shape a client meeting agenda.

# **What to Solve** 

Your solution should address the following core questions:

1. Determine what proportion of that wallet is currently being captured by Syn Bank using the synthetic internal datasets provided.

2. Identify and rank the most attractive revenue growth opportunities based on the gap between total estimated wallet and current share.

3. Show a meaningful Generative AI use case that adds practical value to the solution, rather than serving as a cosmetic extra.

# **Data** 

The datasets provided for the hackathon represent synthetic internal records from Syn Bank and are designed to reflect realistic patterns in South African corporate banking. These include transactional data, SWIFT payment data, trading data, and a mapped set of public financial statement inputs to support external benchmarking and wallet estimation.

Teams are expected to supplement the supplied internal data with publicly available external sources where relevant. Suggested sources include annual financial reports, JSE SENS announcements, National Treasury resources, CIPC records, DealMakers South Africa, Bloomberg open data, and company investor relations pages.

**Provided:**

| Dataset                         | Description                                                                                                                  | Key Fields                                                                                                                                                                                             |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Syn Bank Transactional Data** | Syn Bank's internal transaction ledger across a portfolio of 20 JSE-listed corporate clients                                 | Client ID, transaction date, transaction type, ZAR amount, counterparty, account type, debit/credit                                                                                                    |
| **Syn Bank SWIFT Payment Data** | Cross-border payment messages routed through Syn Bank on behalf of corporate clients                                         | SWIFT message type, originator, beneficiary, currency, ZAR equivalent, value date, country corridor                                                                                                    |
| **Syn Bank Trade Finance Data** | Synthetic trade finance instrument activity (letters of credit, guarantees, export collections) issued for corporate clients | Entity ID, entity name, instrument date, instrument type, direction (import/export), tenor (days), ZAR value, counterparty country, commodity/contract type, status, beneficiary name, reference, memo |


# **Solution Guidance** 

When developing your approach, consider how external financial statement signals can be linked to Syn Bank’s internal records. For example, inventory balances and cost of sales may point to trade finance needs, foreign revenue may imply FX hedging demand, and debt schedules may indicate lending or capital markets opportunities.

Generative AI should be integrated in a way that is useful and measurable. Strong examples include automated extraction of financial statement data, natural language querying of results, anomaly detection with plain-English explanations, client briefing generation, or retrieval-based synthesis of public competitive intelligence.

# **Deliverables** 

Your submission should include the following components:

- A reproducible Python or R notebook showing data ingestion, transformation, modelling, and visualisation.

- A documented methodology explaining assumptions, wallet sizing logic, and limitations.

- Evidence of Generative AI integration, including prompts, workflow, or code used.

- A requirements file or equivalent so that the computational environment can be reproduced.

- An executive dashboard with a portfolio-level summary, client drill-downs, opportunity heatmap, and AI-generated briefing notes for at least three clients.

- A presentation for the judging panel summarising the problem, methodology, AI component, results, and next steps.

# **Rules** 

**All Syn Bank datasets are synthetic and must be treated as confidential within the hackathon environment. Teams may not attempt to link the fictional data to any real bank, client, or transaction, and any external sources used should be properly cited in the solution materials.** 

**Only registered team members may contribute to the submission, and teams may not share code, methodology, or findings with other teams during the build phase. Participants are expected to engage professionally with other teams, mentors, judges, and organisers throughout the event.** 

# **Hints and Tips** 

Past briefs consistently used concise practical guidance, and the new brief should do the same by pointing teams toward commercially meaningful reasoning rather than over-prescriptive modelling choices. 

For this challenge, the most helpful mindset is to start with the business question, use financial statement notes as a rich source of wallet signals, treat gaps in Syn Bank data as evidence of competitor activity, and keep the Generative AI component practical and decision-oriented. 

A strong dashboard should quickly answer where a banker should focus next, while a strong methodology should make assumptions transparent and defensible. Teams that combine rigorous data linkage with clear storytelling are likely to be the most competitive. 