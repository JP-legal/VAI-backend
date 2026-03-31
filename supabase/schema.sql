-- AURA v2 Full Database Schema
-- Run this in Supabase SQL editor

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─────────────────────────────────────────────
-- PROFILES (core user profiles)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  title TEXT,
  bio TEXT,
  company TEXT,
  website TEXT,
  linkedin_url TEXT,
  twitter_url TEXT,
  instagram_url TEXT,
  avatar_url TEXT,
  cover_image_url TEXT,
  account_type TEXT DEFAULT 'personal' CHECK (account_type IN ('personal', 'company')),
  -- AI persona
  ai_persona TEXT,
  ai_tone TEXT DEFAULT 'professional',
  ai_context TEXT,
  scraped_data JSONB DEFAULT '{}',
  -- Language settings (v2)
  default_language TEXT DEFAULT 'en-US',
  language_mode TEXT DEFAULT 'adaptive' CHECK (language_mode IN ('adaptive', 'fixed', 'bilingual')),
  secondary_language TEXT,
  arabic_dialect TEXT DEFAULT 'gulf' CHECK (arabic_dialect IN ('gulf', 'egyptian', 'levantine', 'msa')),
  -- Voice settings (v2)
  voice_enabled BOOLEAN DEFAULT FALSE,
  voice_mode_enabled BOOLEAN DEFAULT FALSE,
  -- Lead capture settings
  calendly_url TEXT,
  whatsapp_number TEXT,
  notification_email TEXT,
  notify_on_lead BOOLEAN DEFAULT TRUE,
  notify_on_message BOOLEAN DEFAULT FALSE,
  -- Meta
  is_published BOOLEAN DEFAULT FALSE,
  plan TEXT DEFAULT 'free' CHECK (plan IN ('free', 'personal', 'pro', 'elite', 'startup', 'business', 'enterprise')),
  credits INTEGER DEFAULT 500,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- VISITORS (cross-profile visitor identity)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS visitors (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  anonymous_id TEXT UNIQUE NOT NULL,
  name TEXT,
  email TEXT,
  phone TEXT,
  country_code TEXT,
  city TEXT,
  browser_language TEXT,
  first_seen_at TIMESTAMPTZ DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ DEFAULT NOW(),
  total_conversations INTEGER DEFAULT 0
);

-- ─────────────────────────────────────────────
-- VOICE PROFILES (ElevenLabs voice config)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS voice_profiles (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE UNIQUE NOT NULL,
  elevenlabs_voice_id TEXT,
  fallback_voice_id TEXT DEFAULT 'pNInz6obpgDQGcFmaJgB',
  is_cloned BOOLEAN DEFAULT FALSE,
  sample_urls JSONB DEFAULT '[]',
  language_mode TEXT DEFAULT 'adaptive',
  default_language TEXT DEFAULT 'en-US',
  secondary_language TEXT,
  arabic_dialect TEXT DEFAULT 'gulf',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- CONVERSATIONS (enhanced v2)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE NOT NULL,
  visitor_id UUID REFERENCES visitors(id),
  -- Identity
  visitor_name TEXT,
  visitor_email TEXT,
  visitor_phone TEXT,
  visitor_country TEXT,
  visitor_city TEXT,
  visitor_language TEXT DEFAULT 'en-US',
  dialect TEXT,
  -- Content
  messages JSONB DEFAULT '[]',
  ai_summary TEXT,
  topics JSONB DEFAULT '[]',
  -- Voice
  mode TEXT DEFAULT 'chat' CHECK (mode IN ('chat', 'voice')),
  has_audio BOOLEAN DEFAULT FALSE,
  audio_url TEXT,
  transcript TEXT,
  -- Outcomes
  lead_captured BOOLEAN DEFAULT FALSE,
  meeting_booked BOOLEAN DEFAULT FALSE,
  escalated_to_human BOOLEAN DEFAULT FALSE,
  -- Meta
  duration_seconds INTEGER,
  message_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  ended_at TIMESTAMPTZ
);

-- ─────────────────────────────────────────────
-- VISITOR MEMORY (per visitor per profile)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS visitor_memory (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  visitor_id UUID REFERENCES visitors(id) ON DELETE CASCADE NOT NULL,
  profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE NOT NULL,
  memory_summary TEXT,
  key_facts JSONB DEFAULT '{}',
  conversation_count INTEGER DEFAULT 0,
  last_updated TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(visitor_id, profile_id)
);

-- ─────────────────────────────────────────────
-- VISITOR CONSENT LOG
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS visitor_consent (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  visitor_id UUID REFERENCES visitors(id) ON DELETE CASCADE NOT NULL,
  profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE NOT NULL,
  consented_to_recording BOOLEAN DEFAULT FALSE,
  consented_to_memory BOOLEAN DEFAULT FALSE,
  ip_address TEXT,
  consented_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- LEADS (quick-access lead table)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leads (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE NOT NULL,
  conversation_id UUID REFERENCES conversations(id),
  visitor_id UUID REFERENCES visitors(id),
  name TEXT,
  email TEXT,
  phone TEXT,
  company TEXT,
  message TEXT,
  source TEXT DEFAULT 'chat',
  status TEXT DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'qualified', 'converted', 'lost')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- CREDIT TRANSACTIONS
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS credit_transactions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  profile_id UUID REFERENCES profiles(id) ON DELETE CASCADE NOT NULL,
  amount INTEGER NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('debit', 'credit')),
  reason TEXT NOT NULL,
  conversation_id UUID,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- INDEXES
-- ─────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_profiles_slug ON profiles(slug);
CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_profile_id ON conversations(profile_id);
CREATE INDEX IF NOT EXISTS idx_conversations_visitor_id ON conversations(visitor_id);
CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_visitors_anonymous_id ON visitors(anonymous_id);
CREATE INDEX IF NOT EXISTS idx_visitors_email ON visitors(email);
CREATE INDEX IF NOT EXISTS idx_leads_profile_id ON leads(profile_id);
CREATE INDEX IF NOT EXISTS idx_visitor_memory_visitor_profile ON visitor_memory(visitor_id, profile_id);

-- ─────────────────────────────────────────────
-- ROW LEVEL SECURITY
-- ─────────────────────────────────────────────
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE voice_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE visitor_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;

-- Profiles: owner can read/write their own
CREATE POLICY "profiles_owner" ON profiles
  FOR ALL USING (auth.uid() = user_id);

-- Profiles: anyone can read published profiles (for public page)
CREATE POLICY "profiles_public_read" ON profiles
  FOR SELECT USING (is_published = TRUE);

-- Conversations: profile owner can read all conversations for their profiles
CREATE POLICY "conversations_owner" ON conversations
  FOR ALL USING (
    profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())
  );

-- Conversations: service role can insert (for public chat)
-- (handled via service role key in API routes)

-- Leads: profile owner only
CREATE POLICY "leads_owner" ON leads
  FOR ALL USING (
    profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())
  );

-- Voice profiles: profile owner only
CREATE POLICY "voice_profiles_owner" ON voice_profiles
  FOR ALL USING (
    profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())
  );

-- Visitor memory: profile owner can read
CREATE POLICY "visitor_memory_owner" ON visitor_memory
  FOR ALL USING (
    profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())
  );

-- Credit transactions: profile owner only
CREATE POLICY "credits_owner" ON credit_transactions
  FOR ALL USING (
    profile_id IN (SELECT id FROM profiles WHERE user_id = auth.uid())
  );

-- ─────────────────────────────────────────────
-- FUNCTIONS
-- ─────────────────────────────────────────────

-- Auto-update updated_at on profiles
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER profiles_updated_at
  BEFORE UPDATE ON profiles
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Deduct credits
CREATE OR REPLACE FUNCTION deduct_credits(
  p_profile_id UUID,
  p_amount INTEGER,
  p_reason TEXT,
  p_conversation_id UUID DEFAULT NULL
) RETURNS BOOLEAN AS $$
DECLARE
  current_credits INTEGER;
BEGIN
  SELECT credits INTO current_credits FROM profiles WHERE id = p_profile_id;
  IF current_credits IS NULL OR current_credits < p_amount THEN
    RETURN FALSE;
  END IF;
  UPDATE profiles SET credits = credits - p_amount WHERE id = p_profile_id;
  INSERT INTO credit_transactions (profile_id, amount, type, reason, conversation_id)
  VALUES (p_profile_id, p_amount, 'debit', p_reason, p_conversation_id);
  RETURN TRUE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
