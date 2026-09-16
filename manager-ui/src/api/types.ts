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
  member_count: number | null;
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
  server_member_count: number | null;
  staff_member_count: number | null;
  latest_member: {
    id: string;
    username: string;
    global_name: string | null;
    avatar: string | null;
    joined_at: number;
  } | null;
  online_staff: {
    id: string;
    username: string;
    avatar: string | null;
    role?: string | null;
    roles: string[];
    status: string;
  }[];
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

export type TicketMessage = {
  id: string;
  author_id: string;
  author_name: string;
  author_avatar: string | null;
  content: string;
  created_at: number;
  attachments: string[];
  is_staff: boolean;
};

export type TicketSummary = {
  guild_id: string;
  channel_id: string;
  number: number;
  opener_id: string;
  opener_name: string | null;
  opener_avatar: string | null;
  section: string;
  section_label: string | null;
  section_emoji: string | null;
  claimed_by: string | null;
  status: string;
  created_at: number;
  motivo: string | null;
  sla_status: string;
};

export type TicketDetail = TicketSummary & {
  messages: TicketMessage[];
  opened_at?: number | null;
  closed_at?: number | null;
  closed_by?: string | null;
  closed_by_name?: string | null;
  close_reason?: string | null;
  transcript_url?: string | null;
};

export type TicketCloseResponse = {
  success: boolean;
  guild_id: string;
  channel_id: string;
  closed_by: string;
  closed_at: number;
  close_reason: string | null;
  close_origin?: string | null;
  transcript_url: string | null;
  message: string;
};

export type TicketReplyResponse = {
  success: boolean;
  guild_id: string;
  channel_id: string;
  message_id: string;
  message: string;
};

export type TicketHistoryItem = {
  guild_id: string;
  number: number;
  channel_name: string;
  section: string;
  section_label: string | null;
  section_emoji: string | null;
  motivo: string | null;
  opener_id: string;
  opener_name: string | null;
  opener_avatar: string | null;
  claimed_by: string | null;
  closed_by: string | null;
  closed_by_name: string | null;
  closed_by_avatar: string | null;
  close_reason: string | null;
  opened_at: number | null;
  closed_at: number | null;
  transcript_sent: boolean;
  transcript_url?: string | null;
};

export type TicketHistoryDetail = TicketHistoryItem & {
  messages: TicketMessage[];
  transcript_url: string | null;
  created_at?: number;
};

export type ApplicationSummary = {
  guild_id: string;
  user_id: string;
  channel_id: string;
  message_id: string;
  notice_message_id: string | null;
  created_at: number;
  status: string;
  candidate_name: string | null;
  candidate_avatar: string | null;
  candidature_type: string | null;
  staffer_name: string | null;
  staffer_avatar: string | null;
};

export type ApplicationDetail = ApplicationSummary & {
  questions: string[];
  answers: string[];
  qa_available: boolean;
  dm_messages: { direction: "staff" | "user"; author_name?: string; author_avatar?: string; content: string; created_at: number }[];
};
