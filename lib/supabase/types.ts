export type AccountType = 'personal' | 'company'
export type LanguageMode = 'adaptive' | 'fixed' | 'bilingual'
export type ArabicDialect = 'gulf' | 'egyptian' | 'levantine' | 'msa'
export type ConversationMode = 'chat' | 'voice'
export type Plan = 'free' | 'personal' | 'pro' | 'elite' | 'startup' | 'business' | 'enterprise'
export type LeadStatus = 'new' | 'contacted' | 'qualified' | 'converted' | 'lost'

export interface Profile {
  id: string
  user_id: string
  slug: string
  name: string
  title?: string
  bio?: string
  company?: string
  website?: string
  linkedin_url?: string
  twitter_url?: string
  instagram_url?: string
  avatar_url?: string
  cover_image_url?: string
  account_type: AccountType
  ai_persona?: string
  ai_tone: string
  ai_context?: string
  scraped_data: Record<string, unknown>
  default_language: string
  language_mode: LanguageMode
  secondary_language?: string
  arabic_dialect: ArabicDialect
  voice_enabled: boolean
  voice_mode_enabled: boolean
  calendly_url?: string
  whatsapp_number?: string
  notification_email?: string
  notify_on_lead: boolean
  notify_on_message: boolean
  is_published: boolean
  plan: Plan
  credits: number
  created_at: string
  updated_at: string
}

export interface Visitor {
  id: string
  anonymous_id: string
  name?: string
  email?: string
  phone?: string
  country_code?: string
  city?: string
  browser_language?: string
  first_seen_at: string
  last_seen_at: string
  total_conversations: number
}

export interface VoiceProfile {
  id: string
  profile_id: string
  elevenlabs_voice_id?: string
  fallback_voice_id: string
  is_cloned: boolean
  sample_urls: string[]
  language_mode: LanguageMode
  default_language: string
  secondary_language?: string
  arabic_dialect: ArabicDialect
  created_at: string
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  language?: string
  audio_url?: string
}

export interface Conversation {
  id: string
  profile_id: string
  visitor_id?: string
  visitor_name?: string
  visitor_email?: string
  visitor_phone?: string
  visitor_country?: string
  visitor_city?: string
  visitor_language: string
  dialect?: string
  messages: Message[]
  ai_summary?: string
  topics: string[]
  mode: ConversationMode
  has_audio: boolean
  audio_url?: string
  transcript?: string
  lead_captured: boolean
  meeting_booked: boolean
  escalated_to_human: boolean
  duration_seconds?: number
  message_count: number
  created_at: string
  ended_at?: string
}

export interface VisitorMemory {
  id: string
  visitor_id: string
  profile_id: string
  memory_summary?: string
  key_facts: Record<string, unknown>
  conversation_count: number
  last_updated: string
}

export interface VisitorConsent {
  id: string
  visitor_id: string
  profile_id: string
  consented_to_recording: boolean
  consented_to_memory: boolean
  ip_address?: string
  consented_at: string
}

export interface Lead {
  id: string
  profile_id: string
  conversation_id?: string
  visitor_id?: string
  name?: string
  email?: string
  phone?: string
  company?: string
  message?: string
  source: string
  status: LeadStatus
  created_at: string
}

export interface CreditTransaction {
  id: string
  profile_id: string
  amount: number
  type: 'debit' | 'credit'
  reason: string
  conversation_id?: string
  created_at: string
}

export const CREDIT_RATES = {
  text_message: 1,
  tts_playback: 3,
  voice_mode_minute: 8,
  voice_clone_setup: 50,
} as const

export const SUPPORTED_LANGUAGES: Record<string, { name: string; elevenlabs_code: string; flag: string }> = {
  'ar-SA': { name: 'Arabic (Gulf / Saudi)', elevenlabs_code: 'ar', flag: '🇸🇦' },
  'ar-EG': { name: 'Arabic (Egyptian)', elevenlabs_code: 'ar', flag: '🇪🇬' },
  'ar-LB': { name: 'Arabic (Levantine)', elevenlabs_code: 'ar', flag: '🇱🇧' },
  'ar-MA': { name: 'Arabic (Moroccan Darija)', elevenlabs_code: 'ar', flag: '🇲🇦' },
  'ar-IQ': { name: 'Arabic (Iraqi)', elevenlabs_code: 'ar', flag: '🇮🇶' },
  'en-US': { name: 'English (American)', elevenlabs_code: 'en', flag: '🇺🇸' },
  'en-GB': { name: 'English (British)', elevenlabs_code: 'en', flag: '🇬🇧' },
  'en-AU': { name: 'English (Australian)', elevenlabs_code: 'en', flag: '🇦🇺' },
  'zh-CN': { name: 'Chinese (Mandarin)', elevenlabs_code: 'zh', flag: '🇨🇳' },
  'zh-TW': { name: 'Chinese (Cantonese)', elevenlabs_code: 'zh', flag: '🇹🇼' },
  'es-ES': { name: 'Spanish (Spain)', elevenlabs_code: 'es', flag: '🇪🇸' },
  'es-MX': { name: 'Spanish (Latin America)', elevenlabs_code: 'es', flag: '🇲🇽' },
  'fr-FR': { name: 'French', elevenlabs_code: 'fr', flag: '🇫🇷' },
  'fr-CA': { name: 'French (Canadian)', elevenlabs_code: 'fr', flag: '🇨🇦' },
  'de-DE': { name: 'German', elevenlabs_code: 'de', flag: '🇩🇪' },
  'pt-BR': { name: 'Portuguese (Brazilian)', elevenlabs_code: 'pt', flag: '🇧🇷' },
  'pt-PT': { name: 'Portuguese (European)', elevenlabs_code: 'pt', flag: '🇵🇹' },
  'ru-RU': { name: 'Russian', elevenlabs_code: 'ru', flag: '🇷🇺' },
  'ja-JP': { name: 'Japanese', elevenlabs_code: 'ja', flag: '🇯🇵' },
  'ko-KR': { name: 'Korean', elevenlabs_code: 'ko', flag: '🇰🇷' },
  'hi-IN': { name: 'Hindi', elevenlabs_code: 'hi', flag: '🇮🇳' },
  'ur-PK': { name: 'Urdu', elevenlabs_code: 'ur', flag: '🇵🇰' },
  'tr-TR': { name: 'Turkish', elevenlabs_code: 'tr', flag: '🇹🇷' },
  'fa-IR': { name: 'Persian (Farsi)', elevenlabs_code: 'fa', flag: '🇮🇷' },
  'id-ID': { name: 'Indonesian', elevenlabs_code: 'id', flag: '🇮🇩' },
  'ms-MY': { name: 'Malay', elevenlabs_code: 'ms', flag: '🇲🇾' },
  'vi-VN': { name: 'Vietnamese', elevenlabs_code: 'vi', flag: '🇻🇳' },
  'th-TH': { name: 'Thai', elevenlabs_code: 'th', flag: '🇹🇭' },
  'pl-PL': { name: 'Polish', elevenlabs_code: 'pl', flag: '🇵🇱' },
  'nl-NL': { name: 'Dutch', elevenlabs_code: 'nl', flag: '🇳🇱' },
  'sv-SE': { name: 'Swedish', elevenlabs_code: 'sv', flag: '🇸🇪' },
  'he-IL': { name: 'Hebrew', elevenlabs_code: 'he', flag: '🇮🇱' },
}

export const DEFAULT_VOICES = {
  male_professional: 'pNInz6obpgDQGcFmaJgB',
  female_professional: 'EXAVITQu4vr4xnSDxMaL',
  male_warm: 'VR6AewLTigWG4xSOukaG',
  female_warm: 'ThT5KcBeYPX3keUtsainK',
} as const
