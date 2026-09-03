import type { PortfolioHolding } from '../../api/types'

interface HoldingsTableProps {
  holdings: PortfolioHolding[]
}

function CostValue({ value }: { value: string | null }) {
  return value === null ? (
    <span className="text-muted-foreground">Unknown</span>
  ) : (
    <span className="tabular-nums">{value}</span>
  )
}

export function HoldingsTable({ holdings }: HoldingsTableProps) {
  return (
    <section aria-labelledby="portfolio-holdings-heading" className="rounded-xl border border-border bg-card">
      <div className="border-b border-border px-5 py-4">
        <h2 className="text-base font-semibold" id="portfolio-holdings-heading">
          Holdings
        </h2>
      </div>
      {holdings.length === 0 ? (
        <p className="px-5 py-10 text-center text-sm text-muted-foreground">No holdings</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[42rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-5 py-3 font-medium" scope="col">
                  Instrument
                </th>
                <th className="px-4 py-3 text-right font-medium" scope="col">
                  Quantity
                </th>
                <th className="px-4 py-3 text-right font-medium" scope="col">
                  Total cost
                </th>
                <th className="px-5 py-3 text-right font-medium" scope="col">
                  Average cost
                </th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((holding) => (
                <tr
                  className="border-b border-border/70 last:border-b-0"
                  key={`${holding.instrument.code}:${holding.instrument.market}`}
                >
                  <th className="px-5 py-3.5 text-left font-medium" scope="row">
                    {holding.instrument.code} <span className="text-muted-foreground">· {holding.instrument.market}</span>
                  </th>
                  <td className="px-4 py-3.5 text-right tabular-nums">{holding.quantity}</td>
                  <td className="px-4 py-3.5 text-right">
                    <CostValue value={holding.total_cost} />
                  </td>
                  <td className="px-5 py-3.5 text-right">
                    <CostValue value={holding.average_cost} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
