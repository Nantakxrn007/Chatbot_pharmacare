export interface Source {
  source: string;
  page: number | null;
  type?: 'external';
  heading?: string;
}

export interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  sources?: Source[];
  timestamp?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
}

export interface Session {
  id: string;
  title?: string;
  patient_name?: string;
  updated_at?: string;
  messages?: Message[];
}

export interface Usage {
  prompt_tokens?: number;
  completion_tokens?: number;
}

export type StreamEvent =
  | { type: 'session'; session_id: string }
  | { type: 'chunk'; content: string }
  | { type: 'done'; sources?: Source[]; usage?: Usage }
  | { type: 'error'; content: string };

export interface Me {
  username: string;
  display_name?: string;
}

export interface TokenSummarySession {
  patient_name?: string;
  title?: string;
  total_prompt?: number;
  total_completion?: number;
}

export interface TokenSummary {
  total_prompt: number;
  total_completion: number;
  sessions: TokenSummarySession[];
}

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';

export interface Patient {
  patient_name: string;
  session_count?: number;
  total_messages?: number;
  first_visit?: string | null;
  last_visit?: string | null;
  has_summary?: boolean;
  summary_updated_at?: string | null;
  risk_level?: RiskLevel | null;
}

export interface RiskAssessment {
  level?: RiskLevel;
  description?: string;
  factors?: string[];
}

export interface TimelineItem {
  date: string;
  summary: string;
}

export interface PatientSummaryData {
  data_sufficient?: boolean;
  risk_assessment?: RiskAssessment;
  conditions?: string[];
  medications_given?: string[];
  allergies?: string[];
  overall_summary?: string;
  timeline?: TimelineItem[];
  recommendations?: string[];
}

export interface PatientSummaryResponse {
  patient_name: string;
  session_count: number;
  first_visit: string | null;
  last_visit: string | null;
  has_summary: boolean;
  summary: PatientSummaryData | null;
  summary_updated_at: string | null;
}

export interface PatientSession {
  id: string;
  title?: string;
  created_at?: string;
  message_count?: number;
}

export interface Drug {
  name: string;
}

export interface TestCase {
  id: string;
  input: string;
  expectation: string;
  case: string;
}

export interface TestCaseSource {
  source: string;
  page?: number | string;
}

export interface TestCaseResult {
  id: string;
  input: string;
  expectation: string;
  prediction: string;
  sources?: TestCaseSource[];
  cosine: number;
  llm_score: number;
  llm_reasoning?: string;
  pass: boolean;
}
