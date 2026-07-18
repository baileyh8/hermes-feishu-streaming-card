# Issue #133 卡片字号配置设计

## 1. 平台能力

飞书 CardKit JSON 2.0 的 markdown/plain-text 组件支持标准字号 10–30px，也支持在 `config.style.text_size` 中定义 PC、mobile 和旧客户端 fallback 的设备差异字号。

官方依据：[富文本（Markdown）组件及设备差异字号](https://open.larksuite.com/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-v2-components/content-components/rich-text)。

整张卡片物理宽高由飞书客户端、消息栏和设备布局控制；HFC 不应提供无法稳定兑现的 `card.width`/`card.height`。组件级宽度也不能改变整卡宽度。

## 2. 目标

1. 允许分别配置正文、reasoning、工具、notice 和 footer 字号。
2. 同时支持简单单值和 PC/mobile 差异字号。
3. 默认配置不改变当前渲染：正文 normal、reasoning small、tool/notice/footer x-small。
4. 多 profile、多 bot 和 per-session card config 使用现有合并优先级。
5. 无效值在启动/doctor 阶段明确报错，不把拼写错误静默降级为 normal。

## 3. 非目标

- 不控制整张卡片像素宽度或高度。
- 不开放任意 CSS、HTML style、margin/padding 或 header/button 字号。
- 不让用户直接写任意 `config.style` JSON。
- 不改变 Markdown 分块、表格预算和 CardKit 元素数量限制。

## 4. 方案比较

### 方案 A：只增加 `body_text_size`、`footer_text_size`

最简单，但不能处理 issue 同时提到的 thinking/timeline，也不能单独保留现有 reasoning/tool 层级。可作为最小补丁，但扩展性差。

### 方案 B：暴露原始 CardKit style JSON

功能最全，但配置不可验证、容易破坏 schema，并扩大支持面。拒绝。

### 方案 C：有限角色映射 + 可选设备差异（推荐）

HFC 定义五个稳定角色和官方字号白名单；标量直接写组件，mapping 自动生成受控的 CardKit custom size。既满足移动端可读性，又保持配置与渲染边界。

## 5. 配置契约

```yaml
card:
  text_sizes:
    body: normal
    reasoning: small
    tool: x-small
    notice: x-small
    footer:
      default: x-small
      pc: x-small
      mobile: notation
```

每个角色接受：

1. 标准字号字符串；或
2. `{default, pc, mobile}` mapping。mapping 至少包含一个字段，缺失字段按 `default -> pc/mobile -> 当前角色默认值` 的顺序补齐。

允许值严格限定为官方枚举：

`heading-0`、`heading-1`、`heading-2`、`heading-3`、`heading-4`、`heading`、`normal`、`notation`、`xxxx-large`、`xxx-large`、`xx-large`、`x-large`、`large`、`medium`、`small`、`x-small`。

未知角色、未知字段、空字符串、非字符串枚举和非 mapping 值都应产生带配置路径的 `ValueError`，例如 `card.text_sizes.footer.mobile`；错误不得打印完整配置。

## 6. 渲染角色映射

- `body`：主回答/公开 thinking 的 `main_content*` markdown。
- `reasoning`：辅助 timeline 中 reasoning 条目。
- `tool`：timeline 工具条目与没有 timeline 时的 tool summary。
- `notice`：timeline notice 以及独立 notice 卡的正文。
- `footer`：完成、运行、等待和失败态 footer。

interaction prompt/header/button 保持 CardKit 默认语义，不跟随 body。附件摘要继续使用正文默认，避免文件名因极小字号不可读。

当角色使用标量时，组件直接设置 `text_size`。当角色使用 mapping 时，renderer 在卡片中生成稳定别名：

```json
{
  "config": {
    "style": {
      "text_size": {
        "hfc_footer": {
          "default": "x-small",
          "pc": "x-small",
          "mobile": "notation"
        }
      }
    }
  }
}
```

对应组件使用 `"text_size": "hfc_footer"`。只为实际使用 mapping 的角色生成 style，避免默认卡 JSON 无意义膨胀。

## 7. 配置与多 profile 传播

`config.py` 负责验证和规范化；`server._resolve_session_card_config()` 继续按 base card、profile、bot 的既有顺序合并。`render_card()` 接收已经规范化的 text-size mapping，不读取全局配置或环境变量。

profile 只覆盖一个角色时，其余角色继承 base/default，不能用浅层替换丢掉其他字号。需要为 `text_sizes` 做受控深合并。

## 8. 向后兼容

- 未配置 `text_sizes` 时生成的 Card JSON 与当前 snapshots 完全一致；正文仍可省略显式 `text_size` 以使用 normal。
- 现有 `footer_fields`、title、timeline 展开、bot/profile 配置不变。
- `normal_v2` 不是官方固定枚举，而是文档示例中的自定义别名；HFC 不直接接受该名字。用户可用 mapping 表达同等 PC/mobile 行为。
- README 示例只展示最常用的 body/footer 配置，详细角色和设备差异放入 user guide。

## 9. 测试设计

### Config

- 标量与 mapping 正常解析。
- 每个无效路径产生精确错误。
- profile 局部覆盖保持其他默认角色。
- config.yaml.example 与 setup 模板一致。

### Render

- 默认配置 Card JSON 与当前行为一致。
- body/footer 标量写入正确 element。
- reasoning/tool/notice 各自只影响目标元素。
- mobile mapping 生成 `config.style.text_size` 和稳定 alias。
- 独立 notice 卡使用 notice 字号。
- 长 Markdown 分块后的每个 chunk 使用同一字号。

### Server/profile

- base、profile、bot 的 text_sizes 深合并顺序正确。
- 同时活跃的不同 bot 卡片不会串用字号。
- streaming update 前后字号稳定，不因 terminal render 丢失。

### 真实飞书验收

至少在桌面端和移动端各检查一张运行卡和完成卡，覆盖正文、reasoning、tool、notice、footer；确认深色模式、长中文、代码块和表格没有异常截断。卡片宽高不作为验收项。

## 10. 文档与示例

`config.yaml.example` 增加注释但保持默认视觉；README 给出“移动端 footer 放大到 notation”的短例；中英文 user guide 列出官方枚举、角色映射、device mapping 和不支持整卡宽高的边界。

## 11. 验收标准

1. 用户能独立调整 body 与 footer，且可只放大 mobile。
2. reasoning/tool/notice 不被 body 配置意外影响。
3. 默认安装的 Card JSON 和视觉保持不变。
4. 无效配置在启动前明确失败，不由飞书客户端静默纠正。
5. 多 profile、多 bot、streaming/terminal 渲染均保持隔离。
