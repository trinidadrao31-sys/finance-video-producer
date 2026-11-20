# 四步工作流数据和宿主接法

一个项目一个状态记录。写事件必须带 expected=最近读取的 revision，按事务提交。用户确认另带当前对象 hash 与 user_message。目录中的 workflow.py 是必须使用的标准库实现（有本地执行能力时），按项目使用独立 SQLite 数据库和 four_events 表，不新增服务依赖。旧四步记录可回读，第一次写入附加状态扩展；已授权生成但缺提交台账的旧项目先核对原任务，经 legacy_reconciled 登记真实核对依据，不把“没有回执”当成“未提交”。旧九步项目不自动导入；重新展示其真实原稿/素材再建立对应记录。

|写事件 op|输入|用途|
|---|---|---|
|init / brief|init 给 idea 原话。brief 的 data 含 type、people、speech、product_mode、assets、missing，可含 intent、ref_needs|角色、产品、图、已有声音分别记录。真实资产含 id、label、use、uri；固定图登记摘要，重要素材提交真实版本。|
|plan|data 含 story、review、references 和 board|第 2 步草案。story 含 synopsis、duration_ms、lines、captions，可含 speech_complete、unresolved、ref_needs。review 是针对同版稿件的 verdict、findings、basis。references 是待制/选图清单，board 是整片时序。更新方案时保留历史事件并撤回旧授权。|
|approve_plan|当前 plan-card 的 hash、user_message，mode 可选 prepare、export、render|确认方案。prepare 允许制作清单内的图和样音；无待选项时可导出或进入待拍。逐段提交另行确认。|
|ref_candidate|data 含 ref_id、candidate_id、spec_hash、uri、sha256、inspection、receipt|保存真实候选、工具回执、检查事实及快照。在途查询原任务。普通临时网址转为稳定的本地快照或经宿主核验的不可变版本资产。|
|revise_ref|当前方案 hash、user_message；data 含 id、prompt，局部改图另带 base_candidate|用户指定只改某项图，不必再次批准整张故事。编辑基于旧候选实际图，更新该项并停在第 3 步。|
|ref_choice|user_message 与 data.ref_id、candidate_id、sha256|只说采用某图时保存当前选择，仍停在素材卡。prepare-card 可读取此选择，不产生生成授权。|
|start_batch|prepare-card 的 hash、mode、user_message、data.selections，可选 edits|选定可看可听的资产与提示词版本。render 准备待拍段；export 只定稿导出。|
|clip|data 含段 id、task_id、attempt、prompt_hash、uri、有效 first_frame/last_frame、qa；续接带 used_frame|记录实际片和检查证据。QA 字段 visual、speech、sync、text、seam、duration 逐项 pass/na；失败或不可判明记录 fail/unknown 及 evidence，不进入可采用清单。|
|accept_batch|user_message 与 data.clips=[{id,task_id},…]|允许一次采用一个或一组已经展示的确切版本，任一过期整组不提交。采用上段后可解锁需其有效末帧的下段。若用户同时明确同意合成，附 `data.assembly_hash`（来自当前 `assembly-card`），在同一事务内采用并授权。|
|retry_clip|user_message、data.id、task_id，可选 prompt、categories、timecode|对指定版本整段重生成；不改词可省略 prompt。重投前交付全文并确认。指定时段或区域的编辑走 repair_plan，详见 [局部返修](../../video-finish/repair.md)。实际错读按 [定点发音返修](../../video-finish/pronunciation.md) 的选择执行。|
|authorize_assembly|当前 `assembly-card` 的 hash、user_message|全部段采用后单独明确授权合成；也可与 accept_batch 合并确认。|
|finish|data 含真实成片 uri、按顺序的已选 tasks、qa 及证据|全段采用且当前清单获合成授权后才可领取合成任务，取得并提交通过整片审查的结果。剪接或回听发现问题先返修；交付通过版本和各段实际输入即可。|

第 2 步用户看每段时长、动作及原句，完整卡片可展开。board 的 segments 每项含 id、keep_ms、subjects、opening、ending、seam、shots、asset_ids、cues、titles；可含 ambience、locks、join、timing_required、user_prompt、inherited_ref_ids。shots 用 end_ms 和 visual；cues/titles 用 id、from_ms、to_ms。首段 seam=opening，后续 cut、match、first_frame 或 dissolve_inside，后续段给 join 和末首状态。锁定台词写在 lines（id/speaker/mode/lang/text），字幕/落版写在 captions。原话、镜头、真实素材与每段时长保持可核验。用户已有校准片段时，user_prompt 原样传递，预审也要读实际全文。

reference 含 id、label、use、purpose、ratio、method、prompt、source_ids，可含 segments 与 base_candidate。真实产品走提供原图或基于它编辑；参考样片默认是 analysis 用途，不能用于生成。计划阶段登记“哪几段需要什么”，不制造假图。拍摄时一般参考走 reference_image，同镜续接将上段已采用片的有效末帧作为 first_frame。若继承图像特征，该特征应该确实出现在末帧内。

只读入口包括 `status-card`（面向用户，`summary` 为同源别名）、`status`（完整记录）、`dispatch`、`artifact N01..N08`、`skill-diff`、`plan-card`、`draft-export`、`image-request REF_ID`、`prepare-card [选择文件]`、`media-request SEG_ID`、`assembly-card`（所有段已有通过审查的结果，返回有序版本与 hash）、`compose-request`（`assembly-request` 为同源别名，须明确合成授权）、`export`。数据库不存在时只读命令不创建空库。选择文件格式为 {"selections":{"BG01":"客厅-v1"},"edits":{}}。`apply` 接受一个 JSON 事件文件。预览请求不会登记提交，禁止据预览直接调用远端；必须先领取。

## R3 逐字与窗口校验

逐字要求在 `brief` 事件的 `data.exact_copy` 保存为列表，每项含 `text` 与稳定的 `line_id`、`caption_id` 之一或两者。音画同文填两个 ID，caption 只引用该 `line_id`；异文分列两条。原文字符串不得 trim、补标点或重排。无此要求可省略。全片明确静音设 `data.audio_policy="mute"` 且 `speech=false`；无台词不等于无音轨。

指定最短停留或已有试读/录音依据的最短口播，把 `min_duration_ms` 写在对应 `shot`、`title` 或 `cue` 上，以实际窗口差校验。跨镜的连续要求按实际可连续看见或听见的区间核验，不给每个镜头重复写整段最短时长。

未采用方案可用 `draft-export --output 新文件.md`，或省略 `--output` 输出正文。母稿逐条使用 JSON 字符串明确边界，逐段提示词逐行原样导出，与当前 `plan-card` 同源；此只读操作不改变 DB 字节和修订号，不授权媒体，且拒绝覆盖已有文件。正式 `export` 仍须定稿。同一 `brief` 在相同当前修订号下重复提交是 no-op，不撤销已有计划或采用；错误的 expected 仍被拒绝。

## 本地 CLI 和新增事件

所有路径由当前真实 Skill 目录、项目目录解析，示例中的变量须先设置为真实路径，禁止把占位文字当作路径执行。仅需 Python 标准库，无网站、服务端或额外包。

```bash
python3 -B "$WF" "$DB" apply "$EVENT"
python3 -B "$WF" "$DB" status-card
python3 -B "$WF" "$DB" artifact N04
python3 -B "$WF" "$DB" skill-diff
# 先运行 segment-text S01 并向用户交付全文，apply present_segment，等待后续回复后 apply approve_segment。
python3 -B "$WF" "$DB" claim-video S01 --expected "$REV"
python3 -B "$WF" "$DB" claim-image ROLE01 --expected 8
python3 -B "$WF" "$DB" claim-compose --expected 9
```

`claim-*` 是原子写入并返回已领取的请求和去重 key：状态为 submitting，尚不能说已提交。只有领取成功的那一次执行才能提交一次；恢复时看已存请求和原任务，不重新调用 claim 获取第二份。宿主按实际工具 schema 映射 request，不把状态附加字段盲传给视频接口。回执使用 `job_update` 写入；同步工具也须先领取，再直接登记 succeeded。没有远端任务号时用工具实际返回的日志/回执句柄，不能捏造。

|op|data 和必要外层字段|语义|
|---|---|---|
|node_start / node_failed|data.node=N01..N08；失败必填 reason|登记实际节点开始/卡点，不虚构完成。|
|pause / resume|user_message；可选 data.reason|阻止新工作或恢复；仍允许查询、收回原任务回执。|
|job_update|data.key/status/evidence；按状态填 task_id、uri、error；工具有真实进度可填 progress|submitting→submitted/running/succeeded/failed/submission_unknown；cancel_requested 不等于 cancelled。提交未知须查询并填 queried=true 才解除。终态不可改写。|
|retry_job|user_message、data.key|只对已失败/已确认取消的当前任务递增 attempt；成功结果返修用 retry_clip。|
|review_clip|data.id/task_id/qa|unknown 保持待复核，fail 保留失败原因，全部必要检查通过才可采用。|
|register_artifact|data.node/path/label/env_id；可选 sha256|必须是真实文件，自动取摘要，记录来源环境；旧版本文件不要覆盖。|
|adopt_skills|user_message；data.hash=skill-diff 的 current_hash；scope=future/rerun；rerun 给 nodes|冻结本次采用的规则指纹；未来不重做旧产物，重跑撤销指定下游授权/成果，保存旧事件及可复用的独立片段。|
|rerun_node|user_message；data.node|当前规则不变，从指定节点重跑。内容节点 N03–N05 用完整 plan 重新提交，下游按新输入复用；指定某一媒体段仍用 retry_clip。|
|legacy_reconciled|user_message；data.no_open_jobs=true、evidence|仅在实际查清旧任务均已结束或未提交后解除旧项目门禁；不能据猜测填写。|
|delivered|data.uri/evidence|实际经交付通道呈现最新成片后登记；不等于用户内容认可。|

board.execution 须记录实际核验的 `tool`、`model`、`verified=true`、`min_seconds`、`max_seconds`；这是宿主根据真实工具填写的配置，不是可调用工具名。模型与配置参与确认卡版本。`min_seconds` 用于补足短段请求，实际有效内容仍按 keep_ms 保留。只导出可以暂不绑定生成能力，实际开拍必须先核验。

## 当前确认事件与实际输入

- `brief-card` 可在只有 brief 时读取。给定目标填 target_duration_ms、单独无词时长 silent_ms，可加口读稿 spoken_zh、实读 read_ms、proposed_segments（段号、起止、内容、理由）。案头估值与实测分别标识。单独确认可 apply `confirm_brief`，含当前 hash 与用户原话；01／02 同轮确认则由 approve_plan 同步登记。
- 角色样音的请求和领取使用 `audio-request REF_ID`、`claim-audio REF_ID --expected REV`；候选、快照、receipt、试听证据、sha256、实际 duration_s 与图片共用资产台账。返修生成样音可在 revise_ref.data 给 voice、provider、sample_text 或 duration_s。
- board.execution（或本段 execution 覆盖）登记真实工具、模型或“宿主默认未公开”、已核实字段／schema 标识、min_seconds、max_seconds、media_roles。`media_roles` 是入口已核实支持的用途集合，不是必填素材清单；本次请求只传实际用到的 reference_image、reference_audio、reference_video、first_frame。没有参考视频时不得新增视频缺件；图片＋样音用已核实支持该组合的 media_to_video。仅核实入口有限制时填写 max_reference_audio_s。board.segments 的 audio_route 可取 native_dialogue、ambience、mute、post_voiceover。post_voiceover 是静音底片，在后期登记同版成片旁白轨。
- `segment-card S01` 返回本段 attempt、hash、完整 request、实际有序资产和修订记录。`segment-text S01` 返回应当直接向用户交付的完整确认单正文。两者均只读。投模前重查宿主工具，逐字展示 prompt 并说明每个资产、参数和限制。正文实际发出后提交 present_segment，data 为 id、content（与 segment-text 全文一致）、message_id，外层 hash 为当前卡摘要。结束该轮，收到用户后续回复时提交 `{"op":"approve_segment","expected":当前修订号,"data":{"id":"S01","presentation_id":"实际交付消息标识","reply_id":"后续用户回复标识"},"hash":"该卡的hash","user_message":"实际回复"}`。再从新 revision 执行 claim-video。hash 随项目、段、尝试、实发内容、执行配置与素材变化。工具回写 job_update，开放模型时写 actual_model／actual_tool。提交状态不明先查询。
- 预估是可选信息：没有工具、查询失败、只有付费提醒或无具体数值时保留真实说明，不单独使用 `node_failed N06`，不增加消耗数值/额度类型必填字段。真实输入、权限或本批确认卡点仍用 `node_failed N06`，在 status-card/dispatch 可见并阻止新的 claim-video、claim-repair-source；原任务仍可查询。只因旧预估门槛挂起的项目，在核对原回执、采用新规则并取得实际生成确认后登记 node_start N06，保存恢复依据；不自动删除失败记录。节点状态不能代替真实生成确认或证明额度，详见 host.md。
- 本段首投前编辑走 revise_segment，data.id 和 prompt 为新全文。返修或失败后重投可给 retry_clip／retry_job 附原因 categories、timecode 和修改后全文；动作、手势等局部可改，锁定原句与时序回 02 修订。通过旧项目升级时先核对在途回执，采用新规则后未提交段重新取卡；能力或声音资产未齐时补方案再确认。
- 只在后期排版的 captions 填 render=post，可附 placement。正式原字随分段卡的 post_titles 和合成清单传递，投模正文只留指定字区。原生画面字沿用现有方式。
- 后期旁白用 post_track 绑定已审底片，data 含 id、当前 task_id、从段内零秒对齐的配音轨 uri、sha256、duration_s、evidence。先核实时窗与逐字读音，再生成 assembly-card；后期轨随本次合成清单一起确认。
- V2.4.1 新编方案自动写入 `board.prompt_format=shooting`。提示词由各段的 `opening`、`shots[].visual`、`ending` 及必要约束组成。多窗口按 `shots` 顺序呈现，画面需精确卡点时设置 `timing_required`。参考原图可加 `reference_purpose`，待制图用 `purpose`。
- 新版确认单和导出中的 `post_voices` 从分镜 `cues` 与逐字母稿生成。它含段内起止、角色、语言和原句，供准备实际 `post_track`。合成请求的 `seams` 含每对相邻段的接法、说明、有效尾帧和首帧。投模正文保留本段的画面与原生声音指令。
