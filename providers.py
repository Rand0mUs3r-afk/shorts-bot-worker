"""Bounded Gemini-first requests with optional Groq fallback."""
import json
import os
import time


class Providers:
    def __init__(self):
        self.gemini = os.getenv('GEMINI_API_KEY', '').strip()
        self.groq = os.getenv('GROQ_API_KEY', '').strip()
        self.disabled = False
        self.client = None
        if not (self.gemini or self.groq):
            raise RuntimeError('ai_configuration')

    def generate(self, prompt):
        import httpx
        names = ([] if not self.gemini or self.disabled else ['gemini']) + (['groq'] if self.groq else [])
        for name in names:
            for attempt in range(5):
                try:
                    if name == 'gemini':
                        from google import genai
                        if self.client is None:
                            self.client = genai.Client(api_key=self.gemini, http_options={'timeout': 120000})
                        text = self.client.models.generate_content(
                            model=os.getenv('GEMINI_MODEL') or 'gemini-flash-latest', contents=prompt,
                            config={'response_mime_type': 'application/json'}).text
                    else:
                        response = httpx.post('https://api.groq.com/openai/v1/chat/completions',
                            headers={'Authorization': 'Bearer ' + self.groq},
                            json={'model': os.getenv('GROQ_MODEL') or 'openai/gpt-oss-20b',
                                  'messages': [{'role': 'user', 'content': prompt}],
                                  'max_completion_tokens': 4096}, timeout=120, follow_redirects=False)
                        response.raise_for_status()
                        choice = response.json()['choices'][0]
                        if choice.get('finish_reason') != 'stop':
                            raise ValueError('incomplete_ai_output')
                        text = choice['message']['content']
                    text = text.strip()
                    if text.startswith('```') and text.endswith('```'):
                        text = '\n'.join(text.splitlines()[1:-1])
                    rows = json.loads(text)
                    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                        raise ValueError('invalid_ai_output')
                    json.dumps(rows, allow_nan=False)
                    return rows
                except Exception as exc:
                    code = getattr(exc, 'code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
                    transient = code in (408, 429, 500, 502, 503, 504) or isinstance(exc, (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError))
                    if not transient or attempt == 4:
                        break
                    time.sleep(min(60, 5 * 2 ** attempt))
            if name == 'gemini':
                self.disabled = True
        raise RuntimeError('ai_failed')
