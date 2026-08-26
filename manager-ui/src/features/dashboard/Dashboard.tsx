import { Hash, House, LifeBuoy, ShieldCheck, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { getDashboard } from "../../api/dashboard.api";
import { ApiError } from "../../api/client";
import type { DashboardSummary, GuildUserProfile } from "../../api/types";

export function Dashboard({ token, guildId, guildName, user, isRefreshing, onUnauthorized }: { token: string; guildId: string; guildName: string; user: GuildUserProfile; isRefreshing: boolean; onUnauthorized: () => void }) {
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    setError(null);

    try {
      setData(await getDashboard(token, guildId, isRefreshing));
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        onUnauthorized();
        return;
      }

      if (cause instanceof ApiError && cause.status === 403) {
        setError("Non hai i permessi per visualizzare questa dashboard.");
      } else if (cause instanceof ApiError && cause.status === 404) {
        setError("Dashboard non disponibile per questo server.");
      } else {
        setError(cause instanceof Error ? cause.message : "Errore di caricamento.");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [token, guildId]);
  useEffect(() => { if (isRefreshing) void load(); }, [isRefreshing]);

  if (loading) return <div className="full-state"><span className="button-spinner" />Caricamento dashboard...</div>;

  if (error || !data) {
    return (
      <div className="state-card state-card--error">
        <strong>Dashboard non disponibile</strong>
        <span>{error}</span>
        <button className="secondary-button" onClick={() => void load()}>
          Riprova
        </button>
      </div>
    );
  }

  const sectionEntries = Object.entries(data.tickets_by_section);
  const onlineStaff = data.online_staff ?? [];

  return (
    <div className={`dashboard ${isRefreshing ? "dashboard--refreshing" : ""}`}>
      <div className="page-heading">
        <div>
          <span className="eyebrow">Overview / {guildName}</span>
          <h1>Buongiorno, {user.global_name ?? user.username} <span className="wave">✦</span></h1>
          <p>Ecco cosa sta succedendo nella tua community oggi.</p>
        </div>

        <div className="heading-meta">
          <span className="live-pill"><i /> Live</span>
          <span>
            Ultimo aggiornamento: {new Date(data.data_freshness_timestamp * 1000).toLocaleTimeString("it-IT", {
              hour: "2-digit",
              minute: "2-digit"
            })}
          </span>
        </div>
      </div>


      <div className="dashboard-grid">

        <section className="panel overview-panel">
          <div className="panel-heading">
            <div>
              <h2><House size={16} /> Panoramica Server</h2>
              <span>Informazioni principali della community</span>
            </div>
          </div>

          <div className="overview-list">
            <OverviewMetric label="Membri totali" value={data.server_member_count} icon={<Users size={20} />} />
            <OverviewMetric label="Ticket aperti" value={data.active_tickets} icon={<LifeBuoy size={20} />} />
            <OverviewMetric label="Staff totale" value={data.staff_member_count} icon={<ShieldCheck size={20} />} />
          </div>
        </section>


        <section className="panel latest-member-panel">
          <div className="panel-heading">
            <div>
              <h2>Ultimo membro entrato</h2>
              <span>Accesso piu recente alla community</span>
            </div>
          </div>

          {data.latest_member ? (
            <div className="latest-member">
              <div className="latest-member-avatar">
                {data.latest_member.avatar
                  ? <img src={data.latest_member.avatar} alt="" />
                  : data.latest_member.username.slice(0, 2).toUpperCase()
                }
              </div>

              <div className="latest-member-copy">
                <strong>{data.latest_member.global_name ?? data.latest_member.username}</strong>
                <span>@{data.latest_member.username}</span>
                <time>
                  {new Date(data.latest_member.joined_at * 1000).toLocaleString("it-IT")}
                </time>
              </div>
            </div>
          ) : (
            <div className="latest-member-empty">N/D</div>
          )}
        </section>


        <section className="panel online-staff-panel">

          <div className="panel-heading">
            <div>
              <h2>
                <span className="online-staff-title-dot" /> Staffer Attualmente Online
              </h2>

              <span>
                {onlineStaff.length
                  ? `${onlineStaff.length} staffer connessi ora`
                  : "Nessuno staffer online"}
              </span>
            </div>
          </div>


          {onlineStaff.length ? (

            <div className={`online-staff-list ${onlineStaff.length > 6 ? "online-staff-list--scrollable" : ""}`}>

              {onlineStaff.map((staffer) => (

                <div className="online-staff-row" key={staffer.id}>

                  <div className="online-staff-avatar">

                    {staffer.avatar
                      ? <img src={staffer.avatar} alt="" />
                      : staffer.username.slice(0, 2).toUpperCase()
                    }

                    <span />

                  </div>


                  <div className="online-staff-copy">

                    <strong>{staffer.username}</strong>

                    <small>
                      <Hash size={10} />
                      {staffer.id}
                    </small>

                    <span className="online-staff-roles-label">Ruoli:</span>
                    <div className="online-staff-roles">
                      {(staffer.roles?.length ? staffer.roles : staffer.role ? [staffer.role] : []).map((role) => (
                        <em key={role}>{role}</em>
                      ))}
                    </div>

                  </div>


                  <span className="online-staff-status">
                    {staffer.status}
                  </span>

                </div>

              ))}

            </div>

          ) : (

            <div className="online-staff-empty">
              Gli staffer online appariranno qui.
            </div>

          )}

        </section>


        <section className="panel section-panel">
          <div className="panel-heading">
            <div>
              <h2>Ticket per sezione</h2>
              <span>Distribuzione attuale</span>
            </div>
          </div>

          <div className="section-list">
            {sectionEntries.map(([label, count]) => (
              <SectionRow
                key={label}
                label={label}
                count={count}
                total={sectionEntries.reduce((sum, [, value]) => sum + value, 0)}
              />
            ))}
          </div>
        </section>

      </div>
    </div>
  );
}


function SectionRow({ label, count, total }: { label: string; count: number; total: number }) {
  const percent = total ? (count / total) * 100 : 0;

  return (
    <div className="section-row">
      <div className="section-row-top">
        <span>{label}</span>
        <strong>{count}<small> ticket</small></strong>
      </div>

      <div className="progress-track">
        <div className="progress-fill progress-fill--purple" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}


function OverviewMetric({ label, value, icon }: { label: string; value: number | null; icon: React.ReactNode }) {
  return (
    <div className="overview-row">
      <span className="overview-label">
        {icon}
        {label}
      </span>

      <strong>
        {value ?? "N/D"}
      </strong>
    </div>
  );
}