export type UserProfile = {
  id: string;
  username: string;
  global_name: string | null;
  avatar: string | null;
  is_bot_operator: boolean;
};

export type AuthSession = {
  session_token: string;
  expires_at: number;
  user: UserProfile;
};

export type GuildSummary = {
  id: string;
  name: string | null;
  icon: string | null;
  role: string;
  is_owner: boolean;
  is_admin: boolean;
  active_tickets_count: number;
};

export type GuildDetail = {
  id: string;
  name: string | null;
  icon: string | null;
  user_role: string;
  is_owner: boolean;
  is_admin: boolean;
  sections_count: number;
  branding: string | null;
  sla_seconds: number;
  claim_timeout_seconds: number;
  inactivity_hours: number;
};

export type GuildUserProfile = UserProfile & {
  guild_id: string;
  guild_role: string;
  is_owner: boolean;
  is_admin: boolean;
};

export type DashboardSummary = {
  guild_id: string;
  active_tickets: number;
  closed_tickets_total: number;
  pending_applications: number;
  tickets_today: number;
  tickets_this_week: number;
  tickets_this_month: number;
  average_resolution_time_seconds: number | null;
  average_first_response_time_seconds: number | null;
  sla_compliance_rate: number | null;
  tickets_by_section: Record<string, number>;
  tickets_by_status: Record<string, number>;
  sla_summary: Record<string, number>;
  data_freshness_timestamp: number;
};
