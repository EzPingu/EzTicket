import {
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  MoreHorizontal,
  TrendingUp,
  Users,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from "recharts";

const chartData = [
  { day: "Mon", opened: 18, closed: 14 },
  { day: "Tue", opened: 24, closed: 19 },
  { day: "Wed", opened: 21, closed: 23 },
  { day: "Thu", opened: 32, closed: 25 },
  { day: "Fri", opened: 27, closed: 30 },
  { day: "Sat", opened: 15, closed: 18 },
  { day: "Sun", opened: 19, closed: 21 },
];

const recent = [
  { initials: "GF", name: "Giulia Ferri", text: "ha preso in carico il ticket #284", time: "2 min fa", color: "coral" },
  { initials: "MR", name: "Marco Rossi", text: "ha chiuso il ticket #281", time: "18 min fa", color: "blue" },
  { initials: "EA", name: "Elena Alberti", text: "ha inviato una candidatura", time: "42 min fa", color: "green" },
];

type Props = { guildName: string; isRefreshing: boolean };

export function Dashboard({ guildName, isRefreshing }: Props) {
  return (
    <div className={`dashboard ${isRefreshing ? "dashboard--refreshing" : ""}`}>
      <div className="page-heading">
        <div>
          <span className="eyebrow">Overview / {guildName}</span>
          <h1>Buongiorno, Leo <span className="wave">✦</span></h1>
          <p>Ecco cosa sta succedendo nella tua community oggi.</p>
        </div>
        <div className="heading-meta"><span className="live-pill"><i /> Live</span><span>Ultimo aggiornamento: ora</span></div>
      </div>

      <div className="kpi-grid">
        <Kpi title="Ticket attivi" value="24" change="+12.5%" detail="vs settimana scorsa" icon={<LifeBuoyIcon />} accent="purple" />
        <Kpi title="Ticket chiusi" value="186" change="+8.2%" detail="questo mese" icon={<CheckCircle2 size={19} />} accent="green" />
        <Kpi title="Candidature" value="8" change="-4.1%" detail="da revisionare" icon={<Users size={19} />} accent="orange" negative />
        <Kpi title="SLA compliance" value="94.8%" change="+2.4%" detail="media mensile" icon={<TrendingUp size={19} />} accent="blue" />
      </div>

      <div className="dashboard-grid">
        <section className="panel chart-panel">
          <div className="panel-heading"><div><h2>Volume ticket</h2><span>Andamento degli ultimi 7 giorni</span></div><button className="more-button"><MoreHorizontal size={18} /></button></div>
          <div className="chart-legend"><span><i className="legend-dot legend-dot--purple" />Aperti</span><span><i className="legend-dot legend-dot--blue" />Chiusi</span><strong>+18.4% <ArrowUpRight size={14} /></strong></div>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 4, left: -24, bottom: 0 }}>
                <defs><linearGradient id="opened" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.32} /><stop offset="100%" stopColor="#8b5cf6" stopOpacity={0} /></linearGradient><linearGradient id="closed" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#38bdf8" stopOpacity={0.2} /><stop offset="100%" stopColor="#38bdf8" stopOpacity={0} /></linearGradient></defs>
                <CartesianGrid stroke="#252936" vertical={false} />
                <XAxis dataKey="day" axisLine={false} tickLine={false} tick={{ fill: "#73798a", fontSize: 11 }} dy={10} />
                <Tooltip contentStyle={{ background: "#171a23", border: "1px solid #303442", borderRadius: 10, color: "#f5f7fb" }} />
                <Area type="monotone" dataKey="opened" stroke="#9b7cff" strokeWidth={2} fill="url(#opened)" />
                <Area type="monotone" dataKey="closed" stroke="#45bff6" strokeWidth={2} fill="url(#closed)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="panel sla-panel">
          <div className="panel-heading"><div><h2>Stato SLA</h2><span>Ticket attivi in questo momento</span></div><button className="more-button"><MoreHorizontal size={18} /></button></div>
          <div className="sla-score"><div className="ring"><div><strong>94.8%</strong><span>in target</span></div></div><div className="sla-copy"><strong>Ottimo lavoro!</strong><span>Il team sta rispettando i tempi.</span></div></div>
          <div className="sla-rows"><SlaRow label="In target" value="22" color="green" /><SlaRow label="In avviso" value="1" color="orange" /><SlaRow label="Fuori SLA" value="1" color="red" /></div>
        </section>

        <section className="panel activity-panel">
          <div className="panel-heading"><div><h2>Attività recente</h2><span>Ultime azioni del team</span></div><button className="text-button">Vedi tutto <ChevronRight size={14} /></button></div>
          <div className="activity-list">{recent.map((item) => <div className="activity-row" key={item.name}><div className={`avatar avatar--${item.color}`}>{item.initials}</div><div className="activity-copy"><strong>{item.name}</strong><span>{item.text}</span></div><time>{item.time}</time></div>)}</div>
        </section>

        <section className="panel section-panel">
          <div className="panel-heading"><div><h2>Ticket per sezione</h2><span>Distribuzione attuale</span></div><button className="more-button"><MoreHorizontal size={18} /></button></div>
          <div className="section-list"><SectionRow label="Supporto generale" count="12" percent={50} color="purple" /><SectionRow label="Partnership" count="7" percent={29} color="blue" /><SectionRow label="Segnalazioni" count="5" percent={21} color="orange" /></div>
        </section>
      </div>
      <div className="dashboard-footer"><span><Clock3 size={14} /> Tempo medio di risoluzione <strong>2h 14m</strong></span><span><CircleAlert size={14} /> 3 ticket richiedono attenzione</span></div>
    </div>
  );
}

function Kpi({ title, value, change, detail, icon, accent, negative = false }: { title: string; value: string; change: string; detail: string; icon: React.ReactNode; accent: string; negative?: boolean }) {
  return <div className="kpi-card"><div className={`kpi-icon kpi-icon--${accent}`}>{icon}</div><span className="kpi-title">{title}</span><div className="kpi-value">{value}</div><div className={`kpi-change ${negative ? "kpi-change--negative" : ""}`}><TrendingUp size={13} />{change}<span>{detail}</span></div></div>;
}
function SlaRow({ label, value, color }: { label: string; value: string; color: string }) {
  return <div className="sla-row"><span><i className={`status-dot status-dot--${color}`} />{label}</span><strong>{value}</strong></div>;
}
function SectionRow({ label, count, percent, color }: { label: string; count: string; percent: number; color: string }) {
  return <div className="section-row"><div className="section-row-top"><span>{label}</span><strong>{count}<small> ticket</small></strong></div><div className="progress-track"><div className={`progress-fill progress-fill--${color}`} style={{ width: `${percent}%` }} /></div></div>;
}
function LifeBuoyIcon() { return <span className="life-icon">◉</span>; }
