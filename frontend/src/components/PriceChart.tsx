import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PricePoint } from '../types'
import { formatDate, formatPrice, formatVolume } from '../utils/format'

const tick = { fill: 'var(--text-muted)', fontSize: 12 }
const shortDate = (value: string) => value.slice(5).replace('-', '.')

export default function PriceChart({ data }: { data: PricePoint[] }) {
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} syncId="price">
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="price_date"
            tickFormatter={shortDate}
            tick={tick}
            tickLine={false}
            axisLine={false}
            minTickGap={32}
          />
          <YAxis
            domain={['auto', 'auto']}
            tickFormatter={formatPrice}
            tick={tick}
            tickLine={false}
            axisLine={false}
            width={68}
          />
          <Tooltip
            formatter={(value) => [`${formatPrice(Number(value))}원`, '종가']}
            labelFormatter={(label) => formatDate(String(label))}
          />
          <Line
            type="monotone"
            dataKey="close_price"
            stroke="var(--accent)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>

      <ResponsiveContainer width="100%" height={90}>
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }} syncId="price">
          <XAxis dataKey="price_date" hide />
          <YAxis
            tickFormatter={(v: number) => `${Math.round(v / 10000).toLocaleString('ko-KR')}만`}
            tick={tick}
            tickLine={false}
            axisLine={false}
            width={68}
          />
          <Tooltip
            formatter={(value) => [`${formatVolume(Number(value))}주`, '거래량']}
            labelFormatter={(label) => formatDate(String(label))}
          />
          <Bar dataKey="volume" radius={[2, 2, 0, 0]}>
            {data.map((point) => (
              <Cell
                key={point.price_date}
                fill={
                  point.change_pct == null || point.change_pct === 0
                    ? 'var(--text-muted)'
                    : point.change_pct > 0
                      ? 'var(--up)'
                      : 'var(--down)'
                }
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
