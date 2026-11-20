# 定位与局部返修

用户要求“指定位置改准”时，先从所指版本实际观察问题，再确定编辑方法。当前工具能接收参考视频只说明可参考生成；局部编辑还须核实原视频、时间区间、空间区域或声音分轨等实际控制字段。

实际读音与正式原句不符时，先走 [定点发音返修](pronunciation.md)。由用户选 Seedance 生成声音候选或导入补丁音频，两条路线均回拼到原片。

## 先让用户看到要修的具体位置

1. 定位原片。保存段号、样片版本、实际视频、内容摘要。用户给整片第 23 秒时，用已选片段的实际保留时长映射到段内秒点，并同时展示两种时间。转场重叠按实际剪辑时间线换算。
2. 取证。画面问题抽出该秒点和前后帧，标明原片尺寸、实际帧时间及问题区域；运动中的对象给覆盖整个缺陷过程的轨迹或逐帧遮罩。声音问题回听前一句尾音至后一句起音，记说话人、原句、实际错读与时间窗。
3. 定义目标。写“当前看到／听到什么 → 应改成什么”。例如“3.20–3.48 秒，人物右手食指与中指粘连 → 两指分开且轮廓连续”，或“6.10–7.05 秒漏念‘价格优’ → 指定角色逐字读出，结束落在 7.05 秒前”。“优化一下”“再自然一点”需要转成可观察的标准。
4. 交付确认。把原帧／声音定位、编辑指令全文、实际原片与素材、时段与区域、保留要求、工具能力和可能影响交给用户。实际交付后登记消息标识，再等待本次回复。

画面坐标使用原视频坐标系的归一化矩形 [x,y,width,height]，各值以整幅宽高为基准。单帧问题根据真实帧率或 PTS 确定其时间范围，通常连同前后过渡检查。编辑一张静帧后，需要将修正传播到涉及的帧并重新合入视频，再检查闪烁、遮挡及动作连续性。

## 选择执行方式

| 问题 | 优先执行 | 需要核实和保留的内容 |
|---|---|---|
| 发音错误，正式原句正确 | [Seedance 出候选后回拼，或导入音频后回拼](pronunciation.md) | 用户选定其中一种。两种均按原片时窗回拼声音并保留原画面。 |
| 漏词、串词等其他声音缺陷 | 原片对应话轮的声音修复或重录后合入 | 核对正式逐字稿、角色声线、句尾时间、环境声与音乐。只有混合音轨时先分轨或明确整段混音一起处理。可见嘴型按原口型时长配音，或用实际支持的口型修复并复查面部。 |
| 一处手指、产品细节、局部文字 | 原片时段内的区域编辑、跟踪贴图或图层替换 | 实际原帧、区域或遮罩、连续帧轨迹、原声、其他区域及总时长。静态字卡可优先在后期图层更正。 |
| 一次动作整个过程错误 | 有时段控制的片段编辑，必要时重生成完整动作镜头再合回 | 动作的起点、完成状态、前后镜头衔接和相应声音。不要只换动作中间一帧。 |
| 当前入口只提供生成能力 | 展示它能执行的最小重生成范围及完整新词，让用户确认 | 明确该范围内画面、声音可能变化。使用 retry_clip 会重生成整段；如要进一步缩小到子镜头，先改分镜与剪辑方案，再确认。 |

替换正式原句、说话人、产品事实或已确认时长时，先回 02 更新母稿。只纠正模型把原句念错时，正式母稿继续作为校对依据。用于重录的音频与角色样音分别标用途；样音负责参考声线，正式配音负责成片内容。

## 执行与验收

有可用编辑入口时采用独立局部返修请求。request 中带 source 原视频及版本、from_ms／to_ms、region、编辑资产、problem、desired、preserve 和真实 execution。声音返修标 audio_component，实际已分轨标 separated_track；可见嘴型标 mouth_plan=existing_timing 或 lip_sync。正式台词从该时间窗的母稿自动带入。

原片和已采用版本先保留。工具返回后保存新候选视频，并交付指定位置的前后对比。逐项记录下列检查的实际观察及时间码。

- target，指定缺陷是否解决。逐帧看该区域或逐句回听实际改动，台词检查同时覆盖漏字与多字。
- preservation，要求保留的其他区域、音轨、角色、产品细节和时间是否符合。
- boundaries，编辑区间前后是否有跳帧、闪烁、响度突变、重复词或断气。

同时完成原有 visual／speech／sync／text／seam／duration 检查。仍有 fail 或 unknown 的候选停在待处理状态。若这次仍没改中，具体写出输入是否正确送达、工具是否支持该控制、实际输出偏差，改变有依据的编辑参数或入口后重交确认；用户明确要同词重试时照常交付新一轮完整输入。

用户看过对比并采用后才替换剪辑选片。有效末帧改变时使真实末帧依赖段待重做；保留同一末帧且摘要核对一致时可继续使用下段。每次替换后重新确认合成清单。

## 事件入口

所有写事件带 expected。repair_plan 另带 user_message，data 至少包含 id、task_id、source_sha256、method、from_ms、to_ms、problem、desired、preserve、execution、assets。method 取 audio_edit 或 video_edit。execution 含 tool、model、verified、schema_evidence、controls；controls 只填写实际核实的 source_video、time_range、audio_edit／video_edit，以及 region、audio_separation、lip_sync 等适用能力。

画面编辑给 region、frame_evidence 和 role=source_frame 的真实原帧资产，并给每帧落在修改区间内的段内 at_ms。声音编辑给 audio_component、audition_evidence，独立声音层附 role=speech_track／music_track／ambience_track 的实际分轨资产，或已核实的分离能力。资产含 uri、sha256、role、purpose。模型或编辑器的请求字段由宿主按实际 schema 映射。

运行 repair-text 段号，向用户交付全文与原片定位图／音频，再提交 present_repair，data 含 id、content、message_id，外层 hash 取 repair-card。随后 approve_repair 绑定同一 hash、presentation_id、后续 reply_id 和用户原话。claim-repair 原子领取实际编辑输入，job_update 沿用任务回执与提交未知保护。

repair_result 保存实际完整视频的 task_id、uri、sha256、media_type=video、duration_ms、first_frame、last_frame、last_frame_sha256、ending_changed、qa、checks。checks 的 target／preservation／boundaries 各含 status 与 evidence，target 另填实际 observed，具体描述指定缺陷现在的表现。采用时 accept_repair 带结果的 digest 作为 hash、data.id／task_id 和 user_message。cancel_repair 取消本地待处理方案，先核实在途任务结束。失败重试时保留该轮记录，取消后制定下一轮计划。
