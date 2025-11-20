from nicegui import ui, app, events
from src.core.project_manager import ProjectManager
from src.ui.components.settings_dialog import SettingsDialog
from src.ai.llm_client import LLMClient
from src.core.tavern_parser import TavernParser
from src.ai.rag_engine import RAGEngine
from src.utils.logger import ConsoleLogger as Log
import os
import asyncio
import json

# 初始化全局单例
pm = ProjectManager()
llm_client = LLMClient()
tavern = TavernParser()
rag = RAGEngine()

app.on_startup(pm.init_db)

# --- CSS ---
ui.add_head_html('''
<style>
    body { font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background-color: #f3f4f6; overflow: hidden; }
    
    /* 卡片样式 */
    .segment-card { background: white; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); border-left: 4px solid transparent; transition: all 0.3s; }
    .segment-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
    
    /* 状态 */
    .processing-card { border-left-color: #6366f1 !important; background-color: #eff6ff; }
    .reviewing-card { border-left-color: #f59e0b !important; background-color: #fffbeb; }
    .warning-card { border-left-color: #ef4444 !important; background-color: #fef2f2; }
    .done-card { border-left-color: #22c55e !important; }
    
    /* 文本框 */
    .q-textarea .q-field__control { padding: 8px; }
    .q-textarea textarea { line-height: 1.8; font-size: 16px; color: #374151; }
    .active-chapter { background-color: #e0e7ff; border-right: 3px solid #6366f1; }
    
    /* 插入按钮 */
    .insert-zone { height: 12px; width: 100%; display: flex; justify-content: center; align-items: center; opacity: 0; transition: opacity 0.2s; cursor: pointer; margin: 2px 0; }
    .insert-zone:hover { opacity: 1; }
    .insert-line { height: 2px; background-color: #6366f1; width: 100%; }
    .insert-btn { background-color: #6366f1; color: white; border-radius: 50%; width: 20px; height: 20px; display: flex; justify-content: center; align-items: center; font-size: 14px; z-index: 10; }
    
    /* 聊天 */
    .chat-bubble { max-width: 85%; padding: 12px 16px; border-radius: 12px; line-height: 1.6; font-size: 15px; position: relative; word-wrap: break-word;}
    .chat-user { background-color: #dbeafe; color: #1e3a8a; align-self: flex-end; border-bottom-right-radius: 2px; margin-left: 20%; }
    .chat-ai { background-color: white; color: #374151; align-self: flex-start; border-bottom-left-radius: 2px; border: 1px solid #e5e7eb; margin-right: 10%; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
</style>
''')

def create_layout():
    settings = SettingsDialog()
    settings.create_ui() 
    
    state = {
        'current_chapter_id': None,
        'current_project_id': None,
        'active_system_prompt': None,
        'active_card_name': '默认 (无人设)',
        'segments': [],
        'is_batch_running': False,
        'stop_signal': False,
        'project_chapters': [],
        'chat_history': [] 
    }
    
    editor_container = None 
    chat_msg_container = None
    project_list_container = None

    # ============================
    # 1. Utils (最先定义)
    # ============================
    def stop_workflow(): state['stop_signal'] = True

    def split_text_to_segments(text):
        if not text: return []
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return [{'original': line, 'revised': ''} for line in lines]

    def merge_segments_to_text():
        lines = []
        for seg in state['segments']:
            content = seg['revised'] if seg['revised'] else seg['original']
            if content.strip(): lines.append(content)
        return "\n\n".join(lines)

    async def extract_file_info(e: events.UploadEventArguments):
        filename = "unknown_file"
        if hasattr(e, 'file') and hasattr(e.file, 'name'): filename = e.file.name
        elif hasattr(e, 'name'): filename = e.name
        content_bytes = b""
        if hasattr(e, 'file') and hasattr(e.file, '_data'): content_bytes = e.file._data
        elif hasattr(e, 'content'): content_bytes = await e.content.read()
        elif hasattr(e, 'file') and hasattr(e.file, 'read'):
            try: content_bytes = await e.file.read()
            except TypeError: content_bytes = e.file.read()
        if not content_bytes: raise ValueError("读取数据为空")
        return filename, content_bytes

    def clean_json_response(text):
        try:
            start = text.find('{'); end = text.rfind('}') + 1
            if start != -1 and end != -1: return json.loads(text[start:end])
        except: pass
        return None

    def reset_persona():
        state['active_system_prompt'] = None; state['active_card_name'] = '默认'
        current_persona_label.text = "当前: 默认"

    def clear_chat(): 
        state['chat_history'] = []; 
        if chat_msg_container: chat_msg_container.clear()
        ui.notify('聊天已清空')

    # ============================
    # 2. Rendering (依赖 Utils)
    # ============================
    def update_segment_original(index, value): state['segments'][index]['original'] = value
    def update_segment_revised(index, value): state['segments'][index]['revised'] = value
    def copy_to_revised(index):
        seg = state['segments'][index]; seg['revised'] = seg['original']; seg['ui_component'].value = seg['original']
        seg['ui_row'].classes(remove='processing-card', add='done-card')

    def refresh_editor_view():
        if not editor_container: return
        editor_container.clear()
        with editor_container:
            with ui.element('div').classes('insert-zone').on('click', lambda: insert_segment(-1)):
                 ui.element('div').classes('insert-line'); ui.label('+').classes('insert-btn')
            for index, seg in enumerate(state['segments']): render_segment_row(index, seg)
    
    def insert_segment(index_after, content=''):
        new_seg = {'original': '', 'revised': content}; state['segments'].insert(index_after + 1, new_seg); refresh_editor_view()
    
    def delete_segment(index):
        if 0 <= index < len(state['segments']): del state['segments'][index]; refresh_editor_view()

    def render_segment_row(index, seg):
        row = ui.row().classes('w-full segment-card mb-0 p-2 items-start no-wrap gap-4 relative')
        seg['ui_row'] = row 
        with row:
            with ui.column().classes('w-[45%]'):
                if not seg['original']:
                    ui.label('✨ 新增段落').classes('text-xs text-green-500 font-bold mb-1')
                    placeholder = "（原文为空）"
                else:
                    ui.label(f'#{index + 1} 原文').classes('text-xs text-gray-400 font-bold mb-1')
                    placeholder = "原文内容"
                original_input = ui.textarea(value=seg['original'], placeholder=placeholder).props('autogrow outlined borderless dense').classes('w-full bg-gray-50 rounded')
                original_input.on('input', lambda e, idx=index: update_segment_original(idx, e.value))

            with ui.column().classes('w-[10%] items-center justify-start pt-6 gap-2'):
                ui.button(icon='auto_fix_high', on_click=lambda: run_segment_rewrite(index)).props('round flat dense color=indigo').tooltip('精修')
                if seg['original']: ui.button(icon='arrow_forward', on_click=lambda: copy_to_revised(index)).props('round flat dense color=grey').tooltip('复制')
                ui.button(icon='delete_outline', on_click=lambda: delete_segment(index)).props('round flat dense color=red size=sm')
                with ui.expansion('', icon='edit_note').props('dense flat header-class=p-1'):
                    seg['prompt_input'] = ui.input(placeholder='指令').props('dense outlined').classes('w-24 text-xs')

            with ui.column().classes('w-[45%]'):
                ui.label('AI 建议').classes('text-xs text-indigo-400 font-bold mb-1')
                revised_input = ui.textarea(value=seg['revised']).props('autogrow outlined dense').classes('w-full bg-white border-indigo-100')
                revised_input.on('input', lambda e, idx=index: update_segment_revised(idx, e.value))
                seg['ui_component'] = revised_input

        with ui.element('div').classes('insert-zone').on('click', lambda idx=index: insert_segment(idx)):
             ui.element('div').classes('insert-line'); ui.label('+').classes('insert-btn')

    # ============================
    # 3. AI Logic
    # ============================
    async def generate_smart_query(target_text, context_prev):
        if not target_text: prompt = f"根据上文：{context_prev[-300:]}，推测关键词。"
        else: prompt = f"根据上文和目标：{target_text}，提取检索关键词。"
        keywords = ""
        try:
            async for token in llm_client.stream_rewrite(target_text, prompt, settings.get_role_config('writer')):
                keywords += token
        except: return target_text
        return keywords.strip()

    async def show_warning_dialog(seg, review_data):
        user_decision = {'action': 'wait'}
        def make_decision(action): user_decision['action'] = action; dialog.close()
        with ui.dialog() as dialog, ui.card().classes('w-full max-w-2xl border-t-4 border-red-500'):
            ui.label('⚠️ 文学总监警告：质量未达标').classes('text-lg font-bold text-red-600')
            ui.label(f"评分: {review_data.get('score')}/10").classes('text-xl font-bold')
            ui.label(f"建议: {review_data.get('suggestion')}").classes('text-gray-600 italic bg-gray-50 p-2 w-full')
            with ui.row().classes('w-full justify-end'):
                ui.button('AI 重写', on_click=lambda: make_decision('retry')).props('outline color=indigo')
                ui.button('强制通过', on_click=lambda: make_decision('accept')).props('flat color=grey')
        dialog.open()
        while user_decision['action'] == 'wait': await asyncio.sleep(0.2)
        return user_decision['action']

    async def run_segment_rewrite(index, specific_instruction=None):
        seg = state['segments'][index]
        target_text = seg['original'] if seg['original'] else "(新增)"
        
        Log.system(f"处理段落 #{index+1}")
        prev_segs = state['segments'][max(0, index-20) : index]
        next_segs = state['segments'][index+1 : min(len(state['segments']), index+5)]
        context_prev_list = [s['revised'] if s['revised'].strip() else s['original'] for s in prev_segs]
        context_prev = "\n".join(context_prev_list)
        context_next = "\n".join([s['original'] for s in next_segs])
        
        rag_context = ""
        if state['current_project_id']:
            search_anchor = target_text if len(target_text) > 5 else (prev_segs[-1]['revised'] if prev_segs else "")
            if search_anchor:
                smart_keywords = await generate_smart_query(search_anchor, context_prev)
                rag_context = rag.search_context(smart_keywords, state['current_project_id'])
                if rag_context: Log.rag(f"RAG: {smart_keywords}")

        local_prompt = seg.get('prompt_input').value if seg.get('prompt_input') else None
        user_prompt = specific_instruction or local_prompt or (prompt_input.value if prompt_input.value else "润色")
        
        style = settings.get_style_config()
        style_prompt = f"【风格】保留{style['retention']}%|扩写{style['expansion']}|尺度{style['safety']}|文风{style['tone']}"

        current_try = 0; final_success = False; feedback = ""
        
        while current_try <= 2:
            current_try += 1
            seg['ui_row'].classes(remove='done-card warning-card reviewing-card', add='processing-card')
            seg['ui_component'].props('loading')
            
            instr = user_prompt + (f"\n\n【审校反馈】：{feedback}" if feedback else "")
            full_instruction = f"{rag_context}\n{style_prompt}\n【上文】：{context_prev}\n【下文】：{context_next}\n【目标】：{target_text}\n【指令】：{instr}"
            if state['active_system_prompt']: full_instruction = f"{state['active_system_prompt']}\n\n{full_instruction}"

            full_result = ""
            Log.writer(f"Writer 尝试 {current_try}")
            try:
                async for token in llm_client.stream_rewrite(target_text, full_instruction, settings.get_role_config('writer')):
                    full_result += token
                    if "\n\n" not in full_result:
                        seg['revised'] = full_result; seg['ui_component'].value = full_result
            except Exception as e: Log.system(f"Err: {e}"); break

            if not settings.is_reviewer_enabled(): final_success = True; break
            
            seg['ui_row'].classes(remove='processing-card', add='reviewing-card')
            review_prompt = f"原文：{target_text}\n改写：{full_result}\n指令：{user_prompt}\n要求：{style_prompt}\n输出JSON: score, reason, suggestion"
            
            review_response = ""
            try:
                async for token in llm_client.stream_rewrite("", review_prompt, settings.get_role_config('reviewer')): review_response += token
            except: pass
            
            review_data = clean_json_response(review_response)
            if not review_data: Log.reviewer("JSON Fail", True); final_success = True; break
                
            score = review_data.get('score', 0)
            passed = score >= settings.get_review_threshold()
            Log.reviewer(f"Score: {score}", passed)
            
            if passed:
                if review_data.get('revised_text'):
                    seg['revised'] = review_data['revised_text']; seg['ui_component'].value = review_data['revised_text']; full_result = review_data['revised_text']
                final_success = True; break
            else:
                feedback = review_data.get('suggestion', '')
                if settings.get_review_mode() == 'manual':
                    seg['ui_row'].classes(remove='reviewing-card', add='warning-card')
                    action = await show_warning_dialog(seg, review_data)
                    if action == 'accept': final_success = True; break
                    elif action == 'retry': continue
        
        seg['ui_component'].props(remove='loading')
        split_results = [p.strip() for p in full_result.split('\n\n') if p.strip()]
        if len(split_results) > 1:
            Log.writer(f"自动分段: {len(split_results)}")
            seg['revised'] = split_results[0]; seg['ui_component'].value = split_results[0]
            for new_text in reversed(split_results[1:]): insert_segment(index, new_text)
        else:
            seg['revised'] = full_result; seg['ui_component'].value = full_result

        seg['ui_row'].classes(remove='processing-card reviewing-card warning-card', add='done-card' if final_success else 'warning-card')

    async def send_chat_msg():
        msg = chat_input.value
        if not msg: return
        chat_input.value = ""
        with chat_msg_container:
            ui.label(msg).classes('chat-bubble chat-user')
            ui.run_javascript(f'document.querySelector(".q-drawer__content").scrollTo(0, 99999)')
        mode = chat_mode.value; chat_config = settings.get_role_config('chat'); context = ""
        if mode == 'chapter':
            current_text = merge_segments_to_text()
            rag_hint = ""
            if state['current_project_id']:
                 smart_key = await generate_smart_query(msg, current_text[-500:])
                 rag_hint = rag.search_context(smart_key, state['current_project_id'])
            context = f"【当前章节】：\n{current_text[:3000]}...\n{rag_hint}"
        else:
            if any(x in msg for x in ["总结", "概括", "讲了什么"]):
                rag_res = rag.search_context("故事简介 大纲 主线", state['current_project_id'])
                context = f"【提示】：用户问全书概括。\n{rag_res}"
            else:
                smart_key = await generate_smart_query(msg, "")
                context = rag.search_context(smart_key, state['current_project_id'])
        
        with chat_msg_container: response_bubble = ui.label('Thinking...').classes('chat-bubble chat-ai')
        full_response = ""; user_msg = f"{context}\n【用户问题】：{msg}"
        try:
            async for token in llm_client.stream_rewrite(user_msg, "", chat_config):
                full_response += token; response_bubble.text = full_response
            Log.system("Chat OK")
        except Exception as e: response_bubble.text = f"Error: {e}"

    # ============================
    # 4. Project & IO (Load/Save)
    # ============================
    async def load_chapter(chapter_id, pid=None):
        content = await pm.get_chapter_content(chapter_id)
        if content is not None:
            state['current_chapter_id'] = chapter_id
            if pid: state['current_project_id'] = pid
            state['segments'] = split_text_to_segments(content)
            if not state['segments']: state['segments'] = [{'original': '', 'revised': ''}]
            refresh_editor_view()
            return True
        return False

    async def refresh_projects():
        projects = await pm.get_projects()
        if project_list_container: project_list_container.clear()
        with project_list_container:
            for p in projects:
                with ui.expansion(p['title'], icon='book').classes('w-full bg-white mb-1'):
                    with ui.row().classes('w-full justify-end px-4 py-2 gap-2'):
                         ui.button(icon='visibility', on_click=lambda _, pid=p['id'], title=p['title']: preview_full_novel(pid, title)).props('flat round dense color=blue').tooltip('预览全文')
                         ui.button(icon='content_copy', on_click=lambda _, pid=p['id']: duplicate_project_action(pid)).props('flat round dense color=green').tooltip('创建副本')
                         ui.button(icon='delete', on_click=lambda _, pid=p['id']: delete_project_confirm(pid)).props('flat round dense color=red').tooltip('删除')
                    chapters = await pm.get_chapters(p['id'])
                    for c in chapters:
                        is_active = state['current_chapter_id'] == c['id']
                        base_class = 'w-full text-left px-4 py-2 text-sm hover:bg-gray-100 cursor-pointer'
                        if is_active: base_class += ' active-chapter'
                        ui.label(c['title']).classes(base_class).on('click', lambda _, cid=c['id'], pid=p['id']: handle_chapter_click(cid, pid))

    async def handle_chapter_click(cid, pid): await load_chapter(cid, pid); state['project_chapters'] = await pm.get_chapters(pid); await refresh_projects()
    async def duplicate_project_action(pid): 
        ui.notify('备份中...'); new_pid = await pm.duplicate_project(pid)
        if new_pid: rag.clone_project_memory(pid, new_pid); ui.notify('✅ 成功'); await refresh_projects()
    async def delete_project_confirm(pid):
        async def do_delete(): await pm.delete_project(pid); rag.delete_project_memory(pid); ui.notify('已删除'); confirm_dialog.close(); await refresh_projects()
        with ui.dialog() as confirm_dialog, ui.card(): ui.label('确认删除？').classes('text-red font-bold'); ui.button('确认', on_click=do_delete).props('color=red')
        confirm_dialog.open()
    async def preview_full_novel(pid, title):
        ui.notify('加载中...'); chapters = await pm.get_chapters(pid); txt = "".join([f"\n\n### {c['title']} ###\n\n{await pm.get_chapter_content(c['id'])}" for c in chapters])
        with ui.dialog() as d, ui.card().classes('w-full max-w-6xl h-[90vh]'): ui.textarea(value=txt).classes('w-full h-full').props('readonly filled'); d.open()

    async def save_changes_internal(silent=False):
        if not state['current_chapter_id']: return
        full_text = merge_segments_to_text(); await pm.update_chapter_content(state['current_chapter_id'], full_text)
        if state['current_project_id']: rag.index_chapter(state['current_project_id'], state['current_chapter_id'], full_text)
        if not silent: ui.notify('✅ 已保存')

    async def open_batch_console():
        if not state['current_project_id']: ui.notify('请先选项目'); return
        all_chapters = await pm.get_chapters(state['current_project_id'])
        last_id = await pm.get_progress(state['current_project_id']); last_idx = -1
        if last_id:
            for i, c in enumerate(all_chapters):
                if c['id'] == last_id: last_idx = i; break
        task_config = {'scope': 'current', 'create_backup': True, 'selected_chapters': set()} 
        def update_scope(v): task_config['scope'] = v; render_chapters()
        def toggle_chapter(ch, value): 
            if value: task_config['selected_chapters'].add(ch['id']) 
            else: task_config['selected_chapters'].discard(ch['id'])
        with ui.dialog() as d, ui.card().classes('w-full max-w-3xl'):
            ui.label('批量任务').classes('text-h6')
            with ui.row().classes('w-full gap-4'):
                with ui.column().classes('w-1/3 border-r pr-4'):
                    ui.radio({'current': '本章', 'all': '全书', 'resume': f'继续(第{last_idx+2}章)', 'custom': '自定义'}, value='current', on_change=lambda e: update_scope(e.value))
                    ui.checkbox('创建副本', value=True).bind_value(task_config, 'create_backup')
                with ui.column().classes('w-2/3 pl-2'):
                    chapter_list_ui = ui.scroll_area().classes('h-64 border rounded p-2 w-full')
                    def render_chapters():
                        chapter_list_ui.clear()
                        with chapter_list_ui:
                            scope = task_config['scope']; targets = []
                            if scope == 'current' and state['current_chapter_id']: targets = [c for c in all_chapters if c['id'] == state['current_chapter_id']]
                            elif scope == 'all': targets = all_chapters
                            elif scope == 'resume': targets = all_chapters[last_idx+1:] if last_idx+1 < len(all_chapters) else []
                            if scope == 'custom':
                                for c in all_chapters: 
                                    is_checked = c['id'] in task_config['selected_chapters']
                                    ui.checkbox(c['title'], value=is_checked, on_change=lambda e, ch=c: toggle_chapter(ch, e.value)).props('dense')
                            else:
                                task_config['selected_chapters'] = set(c['id'] for c in targets)
                                for c in targets: ui.label(c['title']).classes('text-sm border-b')
                    render_chapters()
            with ui.row().classes('w-full justify-end pt-4'): ui.button('启动', on_click=lambda: start_batch_execution(task_config, d, all_chapters)).props('color=indigo')
        d.open()

    async def start_batch_execution(config, dialog, all_chapters):
        selected_ids = config['selected_chapters']
        if not selected_ids: ui.notify('未选择章节'); return
        instruction = prompt_input.value
        if not instruction: ui.notify('请输入指令'); return
        dialog.close()
        pid = state['current_project_id']
        if config['create_backup']:
            ui.notify('备份中...'); new_pid = await pm.duplicate_project(pid, suffix="(精修副本)")
            if new_pid: rag.clone_project_memory(pid, new_pid); pid = new_pid; state['current_project_id'] = new_pid; ui.notify('已切换副本')
            else: return
        state['is_batch_running'] = True; state['stop_signal'] = False; batch_btn.props('loading'); stop_btn.classes(remove='hidden')
        final_chapters = [c for c in all_chapters if c['id'] in selected_ids]
        for idx, ch in enumerate(final_chapters):
            if state['stop_signal']: break
            ui.notify(f'进度: {idx+1}/{len(final_chapters)}', position='top'); Log.system(f"开始处理章节: {ch['title']}")
            await load_chapter(ch['id'], pid); await refresh_projects()
            for i, seg in enumerate(state['segments']):
                if state['stop_signal']: break
                if not seg['original'].strip(): continue
                seg['ui_row'].run_method('scrollIntoView', {'block': 'center', 'behavior': 'smooth'})
                await run_segment_rewrite(i, specific_instruction=instruction); await asyncio.sleep(0.5)
            await save_changes_internal(silent=True); await pm.save_progress(pid, ch['id'])
        state['is_batch_running'] = False; batch_btn.props(remove='loading'); stop_btn.classes(add='hidden'); ui.notify('完成')

    async def handle_novel_upload(e):
        try: fn, cb = await extract_file_info(e); pid = await pm.create_project(fn[:-4] if fn.endswith('.txt') else fn, "导入"); await pm.import_content(pid, cb.decode('utf-8', errors='ignore')); ui.notify('记忆构建中...'); chs = await pm.get_chapters(pid); 
        except Exception as err: ui.notify(f'{err}')
        for c in chs: 
             txt = await pm.get_chapter_content(c['id'])
             if txt: rag.index_chapter(pid, c['id'], txt)
        import_dialog.close(); await refresh_projects(); ui.notify('完成')

    async def handle_card_upload(e):
        try: fn, cb = await extract_file_info(e); path=f"data/presets/{fn}"; 
        except Exception as err: ui.notify(f'{err}')
        with open(path, 'wb') as f: f.write(cb)
        data = tavern.parse_card(path); state['active_system_prompt'] = tavern.generate_system_prompt(data); state['active_card_name'] = data.get('name'); current_persona_label.text = f"当前: {data.get('name')}"; ui.notify('激活')

    async def fetch_models():
        m = await llm_client.get_available_models(settings.get_role_config('writer'))
        if m: ui.notify(f'找到 {len(m)} 个模型'); print(m)
        else: ui.notify('失败')

    # ============================
    # 6. UI Structure
    # ============================
    with ui.dialog() as import_dialog, ui.card(): ui.label('导入').classes('text-h6'); ui.upload(on_upload=handle_novel_upload, auto_upload=True).props('accept=.txt')
    
    with ui.right_drawer(value=False).classes('bg-white border-l w-[500px] flex flex-col shadow-xl') as chat_drawer:
        with ui.row().classes('p-4 border-b items-center'): ui.label('助手').classes('text-lg font-bold'); ui.space(); chat_mode=ui.select({'chapter':'本章','book':'全书'},value='chapter').classes('w-32'); ui.button(icon='delete',on_click=clear_chat).props('flat round dense'); ui.button(icon='close',on_click=lambda:chat_drawer.toggle()).props('flat round dense')
        with ui.scroll_area().classes('flex-grow p-4'): chat_msg_container=ui.column().classes('w-full gap-2')
        with ui.column().classes('p-3 border-t'): chat_input=ui.textarea().classes('w-full'); ui.button('发送',on_click=send_chat_msg).props('icon=send').classes('self-end')

    # 【核心修正】回归计算高度，放弃 Flex
    with ui.left_drawer(value=True).classes('bg-gray-50 border-r w-64') as left_drawer:
        with ui.tabs().classes('w-full text-gray-700') as tabs: t1=ui.tab('书架'); t2=ui.tab('角色')
        with ui.tab_panels(tabs, value=t1).classes('bg-transparent w-full'):
            with ui.tab_panel(t1).classes('p-0'): 
                 # 显式计算: 100vh - 顶栏(56) - Tabs(48) = 104
                 with ui.scroll_area().classes('h-[calc(100vh-105px)] w-full'): project_list_container = ui.column().classes('w-full gap-0')
            with ui.tab_panel(t2).classes('p-4 h-[calc(100vh-105px)] overflow-auto'): ui.upload(on_upload=handle_card_upload, auto_upload=True, label="上传角色").classes('h-20'); ui.button('重置',on_click=reset_persona).props('outline w-full mt-2')

    with ui.header().classes('bg-slate-900 items-center text-white h-14 shadow-lg'):
        ui.button(icon='menu', on_click=lambda: left_drawer.toggle()).props('flat dense color=white'); ui.label('NovelForge AI').classes('text-lg ml-2'); ui.space(); current_persona_label=ui.label('默认').classes('text-xs bg-slate-700 px-2 py-1 rounded mr-4')
        ui.button(icon='chat', on_click=lambda: chat_drawer.toggle()).props('flat dense'); ui.button(icon='settings', on_click=settings.open).props('flat dense'); ui.button(icon='upload', on_click=import_dialog.open).props('flat dense')

    with ui.column().classes('w-full h-[calc(100vh-56px)] p-0 bg-gray-100 flex flex-col overflow-hidden'):
        with ui.row().classes('w-full bg-white p-3 shadow-sm z-10 items-center flex-none'):
            ui.button('保存', on_click=lambda: save_changes_internal(False), icon='save').props('unelevated color=green-7'); ui.separator().props('vertical'); prompt_input=ui.input(placeholder='指令').classes('flex-grow bg-gray-50 rounded px-2').props('dense outlined'); ui.separator().props('vertical'); batch_btn=ui.button('批量任务', on_click=open_batch_console).props('unelevated color=indigo'); stop_btn=ui.button('停止', on_click=stop_workflow).props('outline color=red').classes('hidden')
        with ui.scroll_area().classes('w-full flex-grow p-4'): editor_container=ui.column().classes('w-full max-w-full px-6 gap-4 pb-20'); 
    
    ui.timer(0.1, refresh_projects, once=True)