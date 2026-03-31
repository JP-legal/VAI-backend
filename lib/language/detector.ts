import { claude, CLAUDE_MODEL } from '@/lib/ai/claude'
import { SUPPORTED_LANGUAGES } from '@/lib/supabase/types'

export interface LanguageContext {
  detectedLanguage: string
  detectedDialect: string
  formality: 'casual' | 'formal'
  confidence: number
  shouldSwitch: boolean
  elevenlabsCode: string
}

export async function detectLanguage(
  userMessage: string,
  currentLanguage: string,
  profileLanguageMode: string,
  profileDefaultLanguage: string
): Promise<LanguageContext> {
  if (profileLanguageMode === 'fixed') {
    const lang = SUPPORTED_LANGUAGES[profileDefaultLanguage]
    return {
      detectedLanguage: profileDefaultLanguage,
      detectedDialect: '',
      formality: 'casual',
      confidence: 1,
      shouldSwitch: false,
      elevenlabsCode: lang?.elevenlabs_code || 'en',
    }
  }

  try {
    const response = await claude.messages.create({
      model: CLAUDE_MODEL,
      max_tokens: 200,
      messages: [
        {
          role: 'user',
          content: `Detect the language and dialect of this text. Return JSON only, no other text:
{"language": "ar-SA", "dialect": "Gulf Arabic", "formality": "casual", "confidence": 0.95}

Available language codes: ${Object.keys(SUPPORTED_LANGUAGES).join(', ')}, or any BCP-47 code.
For Arabic, specify dialect: "Gulf Arabic", "Egyptian Arabic", "Levantine Arabic", "Moroccan Darija", "Iraqi Arabic", or "Modern Standard Arabic".

Text: "${userMessage.slice(0, 500)}"`,
        },
      ],
    })

    const raw = response.content[0].type === 'text' ? response.content[0].text.trim() : ''
    const jsonMatch = raw.match(/\{[\s\S]*\}/)
    if (!jsonMatch) throw new Error('No JSON in response')

    const parsed = JSON.parse(jsonMatch[0])
    const detectedLang = parsed.language || currentLanguage
    const lang = SUPPORTED_LANGUAGES[detectedLang]

    return {
      detectedLanguage: detectedLang,
      detectedDialect: parsed.dialect || '',
      formality: parsed.formality || 'casual',
      confidence: parsed.confidence || 0.8,
      shouldSwitch: detectedLang !== currentLanguage && (parsed.confidence || 0) > 0.7,
      elevenlabsCode: lang?.elevenlabs_code || 'en',
    }
  } catch {
    const lang = SUPPORTED_LANGUAGES[currentLanguage]
    return {
      detectedLanguage: currentLanguage,
      detectedDialect: '',
      formality: 'casual',
      confidence: 0.5,
      shouldSwitch: false,
      elevenlabsCode: lang?.elevenlabs_code || 'en',
    }
  }
}
