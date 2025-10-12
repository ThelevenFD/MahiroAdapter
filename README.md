# MahiroAdapter

用于让Maim获取真寻的好感度，需要搭配 **[zhenxun_plugin_MaimAdapter](https://github.com/ThelevenFD/zhenxun_plugin_MaimAdapter)** 使用。

## 功能特性

- 🔄 **自动用户信息获取**：在Maim处理消息前自动获取用户的好感度和态度信息
- 💾 **智能缓存机制**：用户信息缓存10分钟，减少API调用频率
- 🔧 **兼容性支持**：兼容Maim 0.10.0及以上版本
- ⚙️ **可配置选项**：支持自定义API地址、超时时间等配置
- 🐛 **调试模式**：提供详细的调试日志输出

## 安装说明

### 前置要求

- Maim Bot f6e33d8或更高版本
- 真寻Bot插件：zhenxun_plugin_MaimAdapter
- Python 3.8+

### 安装步骤

1. 将本插件文件 `plugin.py` 放置到Maim的插件目录中
2. 确保已安装 `aiohttp` 依赖：
   ```bash
   pip install aiohttp
   ```
3. 在bot_config.toml中的plan_style加入:"reason中必须带有你回复的人的名称并用**()**包裹" 最好不要启用mentioned_bot_reply
4. 重启MaiM

## 配置说明

在Maim的配置文件中添加以下配置项：

```toml
[user_info]
# API基础地址（真寻Bot的地址）
api_base_url = "http://10.255.255.254"
# 请求超时时间（秒）
request_timeout = 5.0
# 是否启用用户信息获取
enable_info = true
# 是否记录信息获取结果
log_info_result = true

[debug]
# 是否启用调试模式
enable_debug = false
```

### 配置参数说明

- `api_base_url`：真寻Bot的API地址，默认为 `http://10.255.255.254`
- `request_timeout`：API请求超时时间，默认为5秒
- `enable_info`：是否启用用户信息获取功能，默认为true
- `log_info_result`：是否在控制台记录信息获取结果，默认为true
- `enable_debug`：调试模式，会输出详细的调试信息，默认为false

## 工作原理

1. **消息拦截**：插件在Maim处理消息前拦截用户消息
2. **用户识别**：从消息中提取用户ID和显示名称
3. **API调用**：向真寻Bot的API发送请求获取用户好感度信息
4. **缓存处理**：将获取到的信息缓存10分钟
5. **Prompt注入**：将用户信息注入到Maim的prompt中，增强AI对用户的理解

## 许可证

本项目采用GNU General Public License v3.0许可证。详见 [LICENSE](LICENSE) 文件。

## 贡献

欢迎提交Issue和Pull Request来改进本项目。

## 更新日志

### v1.0.0

- 初始版本发布
- 支持Maim f6e33d8及以上版本
- 完整的用户信息获取和缓存功能

## 相关项目

- [zhenxun_plugin_MaimAdapter](https://github.com/ThelevenFD/zhenxun_plugin_MaimAdapter) - 真寻Bot的Maim适配器插件
- [Maim Bot](https://github.com/MaiM-with-u/MaiBot) - Maim聊天机器人框架

---

**注意**：使用本插件前请确保已获得相关用户的授权，并遵守当地法律法规。
