# 参与开发

欢迎通过 Issue 反馈问题或提交 Pull Request。请先阅读 README 中的兼容范围。

## 开发环境

使用 Windows、Python 3.11 和 Tkinter，在项目根目录运行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe launch.py --data-dir .\local-data
```

测试默认使用合成图片、临时数据库和模拟设备；不要把真实微信数据库或私人的表情上传。
请说明变更解决的问题、验证方法和未验证的环境。修改重试、去重、核对逻辑时，补充能复现问题的测试；不能把“待核对”直接当成“成功”。

## 版本适配

新微信/小红书版本应提供脱敏的环境信息、结构差异以及真实设备测试结果。修改版本字符串本身不代表完成适配。
调整已适配行为时，保留原有兼容测试；对不确定结果保留人工核对和暂停能力。

## 提交前

- 运行相关测试及完整回归测试。
- 检查未包含用户路径、账号、数据库、密钥、缓存和诊断截图。
- 更新受影响的使用说明与第三方声明。
- 提交贡献即同意将自己有权授权的贡献按本仓库 GPL-3.0-only 许可发布；第三方代码保留其原始许可。
