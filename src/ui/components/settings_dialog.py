from nicegui import ui, events
from src.core.config_manager import ConfigManager
from src.core.st_converter import STConverter
import copy

# 默认 Prompt
DEFAULT_PROMPTS = {
    'writer': """你是一个专业的小说修订AI。
规则：
1. 直接输出修改后的正文内容。
2. 严禁输出废话。
3. 若需分段请用双换行符。""",
    'chat': """你是一个贴心的小说助手。结合记忆库回答问题。""",
    'reviewer': """你是一个严苛的小说文学总监。
输出JSON: {"score": int, "reason": str, "suggestion": str, "revised_text": str|null}
评分标准：10=完美，<8=存在问题。"""
}

DEFAULT_FULL_CONFIG = {
    'writer': {'provider': 'openai', 'api_key': '', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-3.5-turbo', 'temperature': 0.7, 'system_prompt': DEFAULT_PROMPTS['writer']},
    'chat': {'provider': 'openai', 'api_key': '', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-3.5-turbo', 'temperature': 0.7, 'system_prompt': DEFAULT_PROMPTS['chat']},
    'reviewer': {'provider': 'openai', 'api_key': '', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-3.5-turbo', 'temperature': 0.7, 'system_prompt': DEFAULT_PROMPTS['reviewer']},
    'enable_reviewer': False, 'review_threshold': 8, 'review_mode': 'manual',
    'style_matrix': {'retention': 80, 'expansion': 'moderate', 'safety': 'strict', 'tone': 'neutral'}
}

class SettingsDialog:
    def __init__(self):
        self.cm = ConfigManager()
        self.converter = STConverter()
        loaded_data = self.cm.load_config()
        self.config = self._merge_defaults(loaded_data, DEFAULT_FULL_CONFIG)
        self.dialog = None

    def _merge_defaults(self, user_conf, default_conf):
        if not isinstance(user_conf, dict): return copy.deepcopy(default_conf)
        result = copy.deepcopy(default_conf)
        for key, val in user_conf.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = self._merge_defaults(val, result[key])
            else:
                result[key] = val
        return result

    def open(self):
        if self.dialog: self.dialog.open()

    def create_ui(self):
        with ui.dialog() as self.dialog, ui.card().classes('w-full max-w-5xl h-[85vh] flex flex-col'):
            with ui.row().classes('w-full items-center justify-between border-b pb-2'):
                ui.label('AI 引擎与风格控制台').classes('text-h6')
                ui.button(icon='close', on_click=self.dialog.close).props('flat round dense')

            with ui.tabs().classes('w-full text-gray-700') as tabs:
                tab_style = ui.tab('风格控制 (Style)', icon='tune')
                tab_writer = ui.tab('Writer (作家)', icon='edit_note')
                tab_chat = ui.tab('Chat (助手)', icon='chat')
                tab_reviewer = ui.tab('Reviewer (总监)', icon='gavel')

            with ui.tab_panels(tabs, value=tab_style).classes('w-full flex-grow'):
                with ui.tab_panel(tab_style):
                    style = self.config['style_matrix']
                    with ui.column().classes('w-full max-w-2xl gap-6'):
                        ui.label('全局改写参数').classes('text-lg font-bold text-indigo-900')
                        with ui.column().classes('w-full bg-gray-50 p-4 rounded'):
                            ui.label('原文保留度').classes('font-bold')
                            ui.slider(min=0, max=100, step=10).bind_value(style, 'retention').props('label-always color=indigo')
                        with ui.row().classes('w-full gap-4'):
                            with ui.column().classes('w-1/2 bg-gray-50 p-4 rounded'):
                                ui.label('扩写欲望').classes('font-bold')
                                ui.select({'conservative': '保守', 'moderate': '适度', 'massive': '狂野'}, value='moderate').bind_value(style, 'expansion').classes('w-full')
                            with ui.column().classes('w-1/2 bg-gray-50 p-4 rounded'):
                                ui.label('内容尺度').classes('font-bold')
                                ui.select({'strict': '严格', 'loose': '宽松', 'nsfw': '破限'}, value='strict').bind_value(style, 'safety').classes('w-full')
                        with ui.column().classes('w-full bg-gray-50 p-4 rounded'):
                            ui.label('文风倾向').classes('font-bold')
                            ui.select({'neutral': '原著', 'serious': '严肃', 'humorous': '幽默', 'dark': '暗黑', 'poetic': '华丽'}, value='neutral').bind_value(style, 'tone').classes('w-full')

                with ui.tab_panel(tab_writer): self._render_role_panel('writer')
                with ui.tab_panel(tab_chat): self._render_role_panel('chat')
                with ui.tab_panel(tab_reviewer):
                    with ui.row().classes('w-full bg-indigo-50 p-2 rounded mb-4 items-center'):
                        ui.icon('verified_user', size='sm').classes('text-indigo-600 mr-2')
                        ui.label('启用校验').classes('font-bold')
                        ui.space()
                        ui.switch('启用').bind_value(self.config, 'enable_reviewer')
                    with ui.column().bind_visibility_from(self.config, 'enable_reviewer'):
                        with ui.row().classes('gap-4 mb-4'):
                            ui.number(label='及格分', min=1, max=10).bind_value(self.config, 'review_threshold').classes('w-32')
                            ui.radio({'manual': '弹窗人工确认', 'auto': '自动打回重写'}).bind_value(self.config, 'review_mode').props('inline')
                        self._render_role_panel('reviewer')

            with ui.row().classes('w-full justify-end pt-4 border-t'):
                ui.button('重置 Prompt', on_click=self.reset_prompts).props('flat color=red')
                ui.button('保存配置', on_click=self.save_and_close).props('unelevated color=green')

    def _render_role_panel(self, role_key):
        role_conf = self.config[role_key]
        
        # --- 核心修复：万能文件提取器 (内嵌版) ---
        async def handle_preset_import(e: events.UploadEventArguments):
            try:
                # 1. 兼容性提取
                content_bytes = b""
                if hasattr(e, 'file') and hasattr(e.file, '_data'): content_bytes = e.file._data
                elif hasattr(e, 'content'): content_bytes = await e.content.read()
                elif hasattr(e, 'file') and hasattr(e.file, 'read'): content_bytes = e.file.read()
                
                if not content_bytes: 
                    ui.notify('读取失败: 文件为空', type='negative')
                    return

                ui.notify('正在分析预设...', type='info')
                content = content_bytes.decode('utf-8', errors='ignore')
                converted = await self.converter.convert_file_to_prompt(content, role_conf)
                
                role_conf['system_prompt'] = converted
                prompt_input.value = converted
                ui.notify('✅ 预设已应用', type='positive')
            except Exception as err:
                ui.notify(f'导入失败: {err}', type='negative')

        with ui.row().classes('w-full gap-4 h-full'):
            with ui.column().classes('w-1/3 gap-2'):
                ui.select(['openai', 'google'], label='Provider', value=role_conf['provider']).bind_value(role_conf, 'provider').classes('w-full')
                ui.input('API Key', password=True).bind_value(role_conf, 'api_key').classes('w-full')
                ui.input('Base URL').bind_value(role_conf, 'base_url').classes('w-full')
                ui.input('Model').bind_value(role_conf, 'model').classes('w-full')
                ui.slider(min=0, max=2, step=0.1).bind_value(role_conf, 'temperature')

            with ui.column().classes('w-2/3 h-full'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label(f'System Prompt ({role_key})').classes('font-bold text-indigo-500')
                    ui.upload(on_upload=handle_preset_import, auto_upload=True, label="导入 ST 预设").props('flat dense color=indigo icon=auto_fix_high').classes('w-40')
                
                prompt_input = ui.textarea().bind_value(role_conf, 'system_prompt').props('outlined input-style="height: 250px; font-family: monospace"').classes('w-full flex-grow')

    def reset_prompts(self):
        for key in ['writer', 'chat', 'reviewer']:
            self.config[key]['system_prompt'] = DEFAULT_PROMPTS[key]
        ui.notify('已重置 (需保存)', type='info')

    def save_and_close(self):
        self.cm.save_config(self.config)
        ui.notify('✅ 配置已保存', type='positive')
        self.dialog.close()
    
    def get_role_config(self, role_key): return self.config.get(role_key, self.config['writer'])
    def is_reviewer_enabled(self): return self.config['enable_reviewer']
    def get_review_threshold(self): return self.config['review_threshold']
    def get_review_mode(self): return self.config['review_mode']
    def get_style_config(self): return self.config['style_matrix']