from nicegui import ui
from src.ui.main_layout import create_layout

# 页面配置
ui.page_title('NovelForge AI')

# 加载布局
create_layout()

# 启动应用
if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="NovelForge AI",
        port=8080,
        reload=True,  # 修改代码后自动刷新
        dark=False    # 默认亮色主题
    )