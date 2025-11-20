import httpx
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

class LLMClient:
    def __init__(self):
        pass

    async def get_available_models(self, config: dict) -> list:
        """尝试从 API 自动获取模型列表"""
        provider = config.get('provider', 'openai')
        if provider != 'openai': return []
            
        api_key = config.get('api_key', '')
        base_url = config.get('base_url', 'https://api.openai.com/v1')
        
        if not api_key: return []

        try:
            if base_url.endswith('/v1'): url = f"{base_url}/models"
            else: url = f"{base_url}/v1/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    return [item['id'] for item in data.get('data', [])]
        except: pass
        return []

    def _get_llm(self, config: dict):
        provider = config.get('provider', 'openai')
        api_key = config.get('api_key', '')
        
        if not api_key: raise ValueError("API Key 未设置")

        if provider == 'google':
            return ChatGoogleGenerativeAI(
                model=config.get('model', 'gemini-1.5-flash'),
                google_api_key=api_key,
                temperature=config.get('temperature', 0.7),
                top_p=config.get('top_p', 0.9),
                convert_system_message_to_human=True
            )
        elif provider == 'openai':
            kwargs = {
                'model': config.get('model', 'gpt-3.5-turbo'),
                'api_key': api_key,
                'base_url': config.get('base_url', 'https://api.openai.com/v1'),
                'temperature': config.get('temperature', 0.7),
                'streaming': True
            }
            if config.get('presence_penalty'): kwargs['presence_penalty'] = config.get('presence_penalty')
            if config.get('frequency_penalty'): kwargs['frequency_penalty'] = config.get('frequency_penalty')
            return ChatOpenAI(**kwargs)
        else:
            raise ValueError(f"不支持的服务商: {provider}")

    async def stream_rewrite(self, text: str, instruction: str, config: dict):
        llm = self._get_llm(config)
        
        # 【关键修改】优先使用配置中的 system_prompt
        system_prompt_content = config.get('system_prompt', '你是一个小说助手。')
        
        messages = [
            SystemMessage(content=system_prompt_content),
            HumanMessage(content=f"指令：{instruction}\n\n内容：\n{text}")
        ]

        async for chunk in llm.astream(messages):
            if hasattr(chunk, 'content'):
                yield chunk.content