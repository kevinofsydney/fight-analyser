# UFC weight misses

The [CSV](ufc_weight_misses.csv) records reported misses at official UFC event weigh-ins, including misses that led to a cancellation. The initial snapshot covers 1 January 2018 through **UFC 334 on 14 November 2026**. It contains 293 documented official misses and 30 related cases. Historical coverage depends on public reporting.

**Reviewed through:** UFC 334, 14 November 2026 (374 cards reviewed in the initial snapshot).

## Fields

| Field | Meaning |
| --- | --- |
| `card_date` | Local event date in `YYYY-MM-DD`. Use the date of the card even when the weigh-in took place the day before. |
| `fighter_name` | Fighter name as recorded in the source or event listing. |
| `card_name` | Published UFC event name. Names and titles can vary by provider. |
| `record_type` | Classification defined below. |
| `pounds_over` | Overage in pounds. An empty field means the official overage was unavailable. |
| `notes` | Relevant context such as a canceled bout, title alternate, or revised limit. |
| `source_url` | Public report supporting the entry. |

| Record type | When to use it |
| --- | --- |
| `official_miss` | The final official reading exceeded the applicable limit. Includes confirmed misses by official title alternates. |
| `weight_unavailable` | A source reported a failed cut or weight-related withdrawal without an official overage. Leave `pounds_over` empty. |
| `revised_limit` | The limit changed before the official weigh-in because of a reported weight issue. The figure compares the recorded weight with the original limit. |
| `title_eligibility` | A fighter made the original non-title limit but was above the title limit when considered for a replacement title bout. The figure uses the title limit. |

## Update after each card

1. Start with the first UFC card after **Reviewed through**. Find the official weigh-in report on UFC.com or the sanctioning commission's results. If neither gives the needed detail, use a contemporaneous weigh-in report from an outlet such as MMA Junkie, MMA Fighting, or Sherdog. Check for later corrections and record the report URL on each row.
2. Check the full card, including prelims, canceled bouts and official title alternates. Use the last accepted scale reading after any permitted retry. If the fighter made weight on a retry or the commission corrected the reading to within the limit, omit the miss.
3. Subtract the applicable limit from the final reading and keep the reported precision. For example, 148.5 lb against a 146 lb non-title limit is 2.5 lb over. Apply the commission's allowance where applicable, use the exact championship limit for a title bout, and check the contracted limit for catchweights. Use `revised_limit` when the contract changed before the official weigh-in because of a reported weight issue. Omit routine pre-agreed catchweights.
4. Add one row per fighter per card. For a correction, replace the existing row and update its citation. Save as UTF-8, retain the header, and sort by card date, card name and fighter name. Check for duplicate fighter/card pairs, valid HTTPS or HTTP source URLs, and positive numeric overages wherever a figure is present.
5. Advance **Reviewed through** after checking the card even if there were zero misses. If you can't verify a card, record the gap here before advancing. Recheck open gaps and corrections during the next update.

## Using the data downstream

- Resolve the card and fighter to canonical IDs and verify the scheduled opponent when the name or booking is ambiguous. A reported miss can belong to a canceled bout, an alternate, or a fighter whose matchup changed.
- Use `official_miss` for measured miss history. Keep a missing row unknown unless another source establishes that the fighter weighed in and made weight. The CSV contains only exceptions, so it cannot by itself establish a fighter's miss rate.
- For any separately authorized historical feature, follow the repo's strict prior-bout date rule and verify that the measurement was available at the prediction cutoff. Preserve missing values and use the booked bout or weigh-in evidence as appropriate to the feature definition. See [AGENTS.md](../../AGENTS.md) and [model decisions](../../docs/DECISIONS.md).
