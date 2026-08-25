# 科技产业情报与A股观察（GPT模型验收） · 2026-08-24

> 行情交易日：**2026-08-24**；AI状态：**Codex GPT示范：Luna初筛 / Sol深研与校验**。

## 科技前沿深研

### 1. vLLM v0.27.0集中扩展新模型、推理性能与大规模容错能力

**状态：深度结论 · 置信度 91%**

- 该版本包含561次提交、242名贡献者，其中64名为新贡献者。
- Kimi K3在单一版本内获得模型文件、内核、Python与Rust前端、AttnRes、DeepGEMM、量化检查点及共享专家分片等全栈支持。
- DeepSeek-V4相关优化覆盖序列并行、路由与工作区复用、冗余内核消除、KV缓存紧凑化和流水线缓冲区显存节省；发布说明列出多项内核级或端到端增益。
- Model Runner V2扩展到编码器注意力、池化、分类、令牌嵌入及CPU多模态等非生成负载。
- 依赖升级至PyTorch 2.13.0、torchvision 0.28.0和Triton 3.7.1，并被明确标注为破坏性环境变更。

**技术机制：** 该版本沿推理服务栈分层推进：上层增加模型适配、Python/Rust前端及gRPC控制面；执行层通过Model Runner V2覆盖生成与非生成任务；算子层针对注意力、MoE、路由和DeepSeek-V4热点内核减少空启动、冗余计算及同步；存储与分布式层扩展KV卸载、预填充/解码解耦、DP+EP容错和弹性EP准备。其核心不是单一算法，而是模型、运行时、内核、缓存与控制面的协同集成。

**新颖性：** 相对一般的增量版本，主要新意是同一版本内完成Kimi K3全栈落地，并把Model Runner V2、混合模型解耦服务、Rust控制面和下一代硬件适配同时向前推进。发布说明没有提供跨版本统一基准，因此更适合判断为工程集成广度提升，而非已证明的普适性能跃迁。

**成熟度：** 属于可获取的正式版本级交付，模型、量化、API和多硬件支持较完整；但PyTorch升级是破坏性变更，容错框架被注明为“simplified”，弹性EP仍包含异步准备工作，Rubin等支持也被称为早期使能，生产升级仍需回归验证。

**6–24个月影响：** 0-6个月内，直接收益更可能来自新增模型可服务性、特定DeepSeek-V4路径优化和首次请求预热改善；部署方同时需要处理依赖与兼容性迁移。6-24个月内，如果容错、弹性EP、KV分层卸载和异构预填充/解码路由经真实负载验证，vLLM可进一步覆盖大规模、异构及非生成推理，但文档尚不足以确认其稳定性和普适收益。

**产业链影响：**

- 大模型推理服务软件（0-6m / positive）：新增模型、API、非生成负载和预热能力直接扩大可服务范围，并提供多个明确的性能优化入口。
- 分布式推理与KV缓存基础设施（6-12m / positive）：DP+EP容错、弹性EP准备、P/D解耦及多级KV卸载共同增强大规模服务能力，但仍需部署验证。
- AI加速器与异构计算生态（6-12m / mixed）：版本同时推进NVIDIA、ROCm、XPU和CPU路径，有利于扩大硬件覆盖；依赖升级和早期硬件使能也增加适配、构建与回归成本。
- 模型提供方与应用开发者（0-6m / positive）：Kimi K3、Qwen3.5等模型支持及Rust控制面、Cohere和Anthropic相关API能力可缩短接入链路，但效果依赖具体模型和部署配置。

**风险与反面证据：**

- PyTorch、torchvision和Triton的破坏性升级可能引发构建、扩展ABI或既有部署兼容问题。
- 561次提交横跨模型、内核、缓存、分布式和API，变更面较大，发布说明中的局部优化不能替代端到端回归。
- 部分性能数据限定于DeepSeek-V4、特定内核或特定硬件，不能直接外推到所有模型与负载。
- 容错框架仍是简化形态，弹性EP与下一代硬件支持带有早期阶段特征。
- 功能数量和贡献者规模不等同于生产可靠性或用户采用率。
- 多个百分比和倍数来自不同优化点，不能相加，也未给出统一测试环境供横向比较。
- 多硬件支持可能扩大覆盖面，但也可能增加维护矩阵和版本碎片化。

**证据：**

- [Release Notes Highlights](https://github.com/vllm-project/vllm/releases/tag/v0.27.0)：This release features 561 commits from 242 contributors (64 new)! Kimi K3 support with a full stack landing in one release: core model files and kernels ( #50089 , #50000 ), Python ( #50093 ) and Rust ( #50104 ) frontends, AttnRes kernels ( #50090 ), DeepGEMM support ( #50458 ), compressed-tensors quantized checkpoints ( #50500 ), DSpark AR fusion ( #50242 ), and an option to shard the shared expert instead of replicating it ( #50656 ).
- [Release Notes Highlights / DeepSeek-V4 performance push](https://github.com/vllm-project/vllm/releases/tag/v0.27.0)：DeepSeek-V4 performance push : sequence parallelism ( #46789 ), ~2x kernel improvement by skipping empty c128 launches ( #48957 ), 3.4% E2E TTFT from skipping unneeded topk/router ( #49486 ), 3.9% E2E TTFT from workspace reuse ( #49236 ), 1.88x kernel from removing a redundant full kernel ( #50298 ), adaptive topk width (1.0% E2E, #50004 ), 448 MiB GPU memory saved in the PP buffer ( #50312 ), a compact MXFP4 indexer KV cache ( #48993 ), and removal of sparse-MLA q-head padding on FlashInfer >= 0.6.14 ( #48047 ).
- [Release Notes Highlights](https://github.com/vllm-project/vllm/releases/tag/v0.27.0)：PyTorch 2.13.0 upgrade along with torchvision 0.28.0 and Triton 3.7.1 ( #48155 ) — this is a breaking environment change; XPU ( #48677 ) and CPU ( #50412 ) followed to torch 2.13 as well.
- [Release Notes Highlights](https://github.com/vllm-project/vllm/releases/tag/v0.27.0)：Resilient large-scale serving : a (simplified) fault tolerance framework for DP+EP external load-balancer deployments ( #44428 ) and async preparation for elastic EP scaling ( #47288 ).

### 2. ViTacPhys以显式物性预测驱动视触觉自适应抓取

**状态：线索 · 置信度 49%**

- 系统从人类视触觉示范中预测物体质量类别、刚度和摩擦系数类别，再以这些估计作为下游自适应抓取策略的条件。
- 数据集覆盖60个刚性与可变形物体，共1,800次人类抓取示范；质量和摩擦为低、中、高有序类别，刚度为连续回归目标。
- 在已见物体上，论文报告质量准确率97.2%、摩擦系数准确率98.8%、刚度MAPE 5.51%；在已知类别的留出物体上分别为87.5%、97.5%和9.08%。
- 人到机器人迁移使用有限遥操作数据、机器人风格视频增强和动作匹配的人类示范。
- 物性条件策略报告ID物体总抓取成功率95.0%、OOD物体83.4%；论文将其定位为系统级可行性研究。

**风险与反面证据：**

- 独立审计降级：“结构化物性条件有望成为物流与家用抓取策略的辅助模块”属于跨场景外推；材料仅将物流和家用机器人作为动机示例，实验未验证这些部署场景。
- 独立审计降级：“人类示范可能提高采集效率”沿用了论文对可扩展、低成本采集的宣传性表述，但材料没有与遥操作或其他采集方式的定量成本、速度对照。
- 60个物体和单一参与者的数据规模有限，不能证明跨操作者、类别、机器人平台和真实长期运行的泛化。
- 质量与摩擦系数被离散为三类，可能丢失精细控制所需的连续差异。
- 刚度测量包含物体、手、传感器安装和接触顺应性；摩擦系数也是物体与硅胶接触面的配对属性，跨硬件迁移可能需要重新标定。
- VLM先验来自接触前视觉，遇到伪装材质、不可见填充状态或新类别时可能产生偏差，并传递给下游策略。
- 抓取成功率和力曲线来自论文给定评测设置，尚无更大规模、多任务或长期稳定性证据。
- 端到端策略可能直接从视触觉序列学习所需控制，不一定需要显式物性预测这一中间瓶颈。
- 论文报告的是条件策略与更高成功率的关联，有限评测不足以隔离数据增强、遥操作数据和物性令牌各自的因果贡献。
- 类别边界在全部标注物体上预先拟合并固定，虽未按测试表现重估，但跨数据集的边界可迁移性仍未得到证明。

**证据：**

- [Abstract](http://arxiv.org/abs/2608.21355v1)：On held-out objects from known categories, it achieves87.5% mass accuracy,97.5%friction-coefficient accuracy, and9.08% stiffness MAPE. We transfer ViTacPhys from the human to the robot domain using limited teleoperation data, robot-style video augmentation, and matched-action human demonstrations, and deploy it as an online module for adaptive grasping.
- [Fig. 4 caption](http://arxiv.org/abs/2608.21355v1)：Fig. 4. Overview of ViTacPhys. (A) The predictor combines temporal visual–tactile observations, flow features, and a VLM-derived semantic prior computed from pre-contact RGB frames. (B) Human-to-robot transfer uses robot teleoperation, matched-action human demonstrations, and visually augmented human demonstrations. (C) During deployment, the VLM prior is computed before contact. After contact, the system immediately begins rolling prediction from a 30-frame visual–tactile queue; unavailable initial entries repeat the earliest post-contact observation before 15 frames and 14 adjacent flow fields are sampled. Cumulative temporal voting stabilizes the predicted classes and stiffness bin before they are passed to the downstream policy.
- [Abstract](http://arxiv.org/abs/2608.21355v1)：The resulting physical-property-conditioned policy achieves95.0%total grasp- ing success on ID objects and83.4%on OOD objects.
- [Section III-B Data Annotation](http://arxiv.org/abs/2608.21355v1)：Because it includes object deformation and hand, sensor-mount, and contact compliance, this operational stiffness is not an intrinsic material constant. Friction Coefficient.The object is placed on a silicone-coated inclined plane matching the tactile contact material. At the critical sliding angleθ, the contact-pair coefficient isµ s = tan(θ); it is not an object-only property.

### 3. OmniAssistBench以固定先验路径模拟全模态助理交互，现有模型仍受视觉提示、长期记忆和延迟响应能力制约

**状态：深度结论 · 置信度 90%**

- 基准通过从互联网视频反推用户目标与操作路径、切分多轮片段，离线模拟连续的人机助理交互，建设耗时超过1000个专家工时。
- 数据集包含300段视频、685组问答，覆盖7类主要任务、16个子任务，并包含3个平均约15轮交互的实拍真实场景。
- 闭源模型Gemini-3-Pro总分为66.4，开源模型Qwen3-Omni-Instruct为51.2；论文将主要缺口归纳为视觉提示跟随、长期记忆、延迟响应和跨轮目标保持。
- 模型上下文容量在长视频中快速耗尽：论文给出的实验设置下，MiniCPM-o-2.6约能保留380秒视频，而Qwen系列有效记忆约80秒。

**技术机制：** 基准先从源视频总结一条预定义的过程先验，要求模型沿唯一指定路径指导用户，从而控制同一目标存在多条合法路径造成的离线评测分叉。随后把源视频按问答时间点切成连续片段，将语音问题嵌入片段末尾，手势或手写提示以画中画方式嵌入；高级任务同时提供用户目标和过程先验。答案采用“Ground Truth Sentence+1至3个Key Points”，由GPT-5按五级规则评判语义正确性、关键点覆盖和冗余或幻觉，并归一化为百分制。长交互超过上下文窗口时使用先进先出方式删除最早历史。

**新颖性：** 核心创新不是增加静态视频问答数量，而是把模型回答会改变用户后续行动这一交互问题显式纳入评测设计；通过源视频反推固定路径，使预录视频仍可用于多轮助理式评测。两层任务体系同时测量基础感知、目标导向过程跟踪、主动等待以及三个复合真实场景。

**成熟度：** 处于研究基准和离线仿真阶段。数据经过重人工设计与视频编辑，任务覆盖较完整，但测试仍依赖预定义路径、预录视频、模型特定采帧及上下文管理策略，不能等同于开放环境中的实时闭环助理可靠性。

**6–24个月影响：** 未来6至24个月，该基准更可能推动模型侧开发视频记忆压缩、用户与背景说话人区分、视觉手势指令对齐、事件触发式等待和跨轮目标状态管理，而不仅是继续扩大上下文窗口。论文中的降分辨率和teacher-forcing结果表明，单纯增加可容纳帧数或替换正确历史不足以解决全部问题，竞争焦点可能转向持续状态表示与多模态注意力控制。

**产业链影响：**

- 实时全模态助理与智能终端（6-12m / mixed）：基准提供了可重复的产品能力验收框架，但66.4的最高总分及多类交互失败说明近期更适合受控辅助，而非高可靠自主指导。
- 长视频推理与记忆基础设施（6-12m / positive）：长交互中的上下文耗尽和跨轮遗忘直接强化了对视频摘要、状态缓存、检索式记忆及低成本长上下文推理的需求。
- 无障碍与主动式人机交互应用（12-24m / mixed）：盲人辅助、手势指令和事件触发响应显示明确应用空间，但地图理解、目标人物识别和正确等待仍未达到可靠部署水平。

**风险与反面证据：**

- 固定先验只保留一条交互路径，可能把现实中同样有效的替代操作判为偏离目标。
- 互联网视频反推和离线切片不能完整复现模型输出实时改变用户行为的闭环反馈。
- 不同模型采用不同视频预处理方式，且长输入触发FIFO历史删除，模型间分数同时受评测管线影响。
- 自动评分依赖LLM judge；尽管不同judge相关性较高，仍不能消除开放答案评分偏差。
- 基准仅有三个复杂真实场景，难以覆盖开放环境的全部异常与安全边界。
- teacher forcing虽提供正确历史，但提升并不总是明显，说明错误累积不是多轮失败的唯一原因。
- 把Qwen3-Omni-Instruct输入缩至360p可多容纳约50秒视频，却没有带来全基准明显变化，说明上下文长度并非唯一瓶颈。
- 主动响应任务在移除音频后反而提升，表明更多模态信息有时会引入背景语音干扰，而不是稳定增益。

**证据：**

- [摘要](http://arxiv.org/abs/2608.21360v1)：To solve the issue of diverging interaction paths (where the same user goal can be achieved through various methods), we provide models with predefined priors derived from the source video, requiring them to guide users along the exact same routes. Since real interaction videos are rare, we construct the dataset by reverse-engineering existing In- ternet videos. We deduce logical user goals and segment the videos into multi-turn clips to simulate continuous inter- actions. This rigorous pipeline requiredover 1000 expert person-hoursto build the dataset.
- [第4.2.1节 Overall Performance](http://arxiv.org/abs/2608.21360v1)：Tables 2 and 3 present the overall performance of all eval- uated models on the Basic and Advanced Interaction Un- derstanding tasks, respectively. The leading closed-source model,Gemini-3-Pro, achieves a score of66.4, while the top-performing open-source model,Qwen3-Omni-Instruct, scores51.2. According to our scoring rubric, these results suggest that current MLLMs generally succeed in under- standing verbal prompts but struggle with providing accurate and comprehensive answers.
- [第4.2.3节 Advanced Tier Performance](http://arxiv.org/abs/2608.21360v1)：One primary challenge comes from limitation of model con- text length. With a 32k context window, MiniCPM-o-2.6 can retain approximately 380 seconds of video at 1 fps. Beyond this limit, the model starts to produce nonsensical words. In contrast, the Qwen family encodes each video frame into a larger number of tokens, resulting in a shorter effective memory of only around 80 seconds. These context capacities are insufficient to preserve a complete interaction history for the multi-turn tasks in our benchmark. Without specialized mechanisms for long-term memory, context limitations are likely to become a critical bottleneck for current models.
- [第4.3.4节 Influence of Input Video Resolution](http://arxiv.org/abs/2608.21360v1)：As shown in Fig. 11, although holding more video frames in the model context slightly increase the scores on most multi-turn questions, there is no obvious change on the scores across the whole benchmark. On one hand, 50 sec- onds longer context may still be insufficient for long videos that may last more than 10 minutes, such as videos in the Real-World Cases task. On the other hand, context limitation may not be the only bottleneck that prevent the model from giving valid responses.

### 4. 自我改进流水线无需均匀配置模型容量：生成器与修订器更敏感，小型批评器已有稳定价值

**状态：线索 · 置信度 49%**

- 论文在规划、摘要、逻辑推理、代码优化和故事生成五类基准上，使用Qwen3六种规模和Gemma 3四种规模开展逐阶段容量扫描。
- 控制实验每次只改变生成器、批评器或修订器中的一个模型，另外两阶段保持固定，并设置无批评器的匹配基线。
- 两个模型家族中，端到端性能对生成器和修订器规模更敏感，对批评器规模相对不敏感；更大的批评器通常只带来边际改善。
- Qwen3-32B配置的30条修订器流水线中有12条低于初始生成；对最弱0.6B修订器的50个退化样本分析显示，41个样本在获得非误导性批评后仍因错误改写而退化。
- 即使采用Qwen3-0.6B作为批评器，相比匹配的无批评基线，五个基准的生成器和修订器扫描均出现正向平均增益。

**风险与反面证据：**

- 独立审计降级：展望中提出按任务风险或批评不确定性动态升级模型，论文未实验动态路由、风险分层或批评不确定性估计。
- 独立审计降级：产业影响中称该策略可降低固定三阶段流水线的推理成本，论文只研究模型容量与任务指标，未报告端到端token、延迟、吞吐或实际成本；只能视为待验证假设。
- 结论来自单轮标准三阶段结构，不能直接外推到包含检索、工具、长期记忆或多次反思的Agent。
- 只测试Qwen3和Gemma 3，模型训练方法、架构或专用批评数据变化后，阶段敏感度可能不同。
- 五个基准均使用受限子集，最难样本被部分排除，生产任务的分布外难度未被验证。
- 批评质量人工分析每个模型规模仅随机抽取有限样本，可能不足以描述低频但高危的误导反馈。
- 成本结论没有直接报告端到端延迟、并发吞吐或不同阶段token消耗，容量缩小不必然等同于系统总成本同比下降。
- 大批评器虽然对端到端指标提升有限，但人工分析显示其往往能发现更多错误；在高风险任务中，完整性价值可能高于平均分增益。
- 批评器规模不敏感可能部分源于修订器无法充分利用更完整的反馈，而不代表批评本身没有额外信息。
- 最弱修订器的退化既发生于正确批评，也发生于误导性批评，说明单纯加强批评器不能替代修订阶段的容量与鲁棒性。

**证据：**

- [第3.2节 Stage-wise Model Size Analysis Protocol](http://arxiv.org/abs/2608.21345v1)：Our objective is to quantify the contribution of model size at each stage of the self-refinement pipeline. To isolate the effect of an individual stage, we vary exactly one stage while keeping the re- maining two stages fixed to the same models. This controlled design ensures that any performance dif- ferences can be attributed solely to the stage being varied (Figure 2).
- [第5节 Results](http://arxiv.org/abs/2608.21345v1)：A consistent trend across all benchmarks is that scaling the size of the generator and the refiner substantially enhances the performance, which is visualized by the steeper lines in Figure 4 and quan- titatively summarized in Table 1. In contrast, the critic stage is relatively insensitive to model size, as larger critic models provide only marginal gains over smaller ones. Results for the Gemma 3-27B, Qwen3-14B, and Qwen3-8B configurations are pro- vided in Appendix C, D, and E. They exhibit the same qualitative trends.
- [第5.3节 Model Size Analysis of The Refiner](http://arxiv.org/abs/2608.21345v1)：Under the Qwen3-32B config- uration, 12 of the 30 evaluated refiner pipelines (5 benchmarks × 6 refiner sizes) perform worse than the corresponding initial generation. The com- plete pipelines of all degraded configurations are in Appendix F. To understand the underlying failure mode, we manually analyzed 50 degradation events from the most extreme case, P(32B,32B,0.6B), where the weakest refiner exhibits the greatest per- formance drop. In all 50 events, the 0.6B refiner produced a worse output than both the initial gen- eration and the 32B refiner under the same initial solution and critique.
- [Limitations](http://arxiv.org/abs/2608.21345v1)：This work analyzes stage-wise model scaling in the canonical generate–critique–refine pipeline with a single refinement iteration. Our findings may not directly generalize to more complex agentic sys- tems that incorporate additional components such as retrieval, planning, tool use, memory, or multiple refinement rounds.

### 5. VIALS揭示生命科学专业图像理解仍是多模态模型瓶颈，工具辅助显著增益但成本与残余误差突出

**状态：线索 · 置信度 49%**

- VIALS包含161个开放回答视觉问答任务，覆盖系统发育树、流式细胞图、印迹与凝胶、质粒图、细胞计数、蛋白结构和小分子结构等专业工作产物。
- 任务来源包括未发表真实实验数据、科学软件程序化生成产物和CC-BY开放论文，强调真实研究中的非规整数据；任务由领域专家创建并经多轮独立复核。
- 直接视觉推理中，GPT-5.6 Sol与Gemini 3.7 Flash并列最高，仅为26.5%平均准确率；要求三次重复均正确时，两者可稳定处理的任务低于17%。
- 跨模型83%至93%的错误属于视觉计数、选错数值或特征、遗漏证据、误解表示规则这四类，表明主要缺口先发生在读取和解释科学图像阶段。
- 代码工具允许模型迭代裁剪、放大和测量后，准确率最多提高43个百分点，但token成本增至直接推理的10至432倍；最佳Agent仍只解决约65%的任务。

**风险与反面证据：**

- 独立审计降级：展望提出短期采用置信度门控，但论文仅评估模型自报置信度且显示严重失准，高置信回答最高准确率仅30%；除非另有外部校准机制，该建议不受当前材料支持。
- 独立审计降级：将结果具体外推为药物发现与高内涵筛选流程中的预筛和第二读者价值，超出了单项科学图像问答基准；论文提及药物发现和assay screening的相关性，但未验证端到端筛选决策或生产部署收益。
- 161个任务不足以覆盖生命科学全部专业图像、实验噪声和复杂解释活动。
- 开放任务公开后可能发生针对性优化，论文虽计划后续刷新，当前版本仍存在过拟合风险。
- LLM-as-a-judge虽经大样本人工验证，但不能完全排除新模型输出形式导致的评分漂移。
- 工具辅助结果高度依赖Agent harness、工具调用策略和巨量token预算，难以直接转化为生产成本收益。
- 基准聚焦相关博士能快速完成的任务，没有覆盖更具歧义、需要实验上下文或后续研究决策的复杂场景。
- 工具辅助最高可提升43个百分点，说明部分科学知识已经存在于模型中，当前低分不应全部解释为领域知识缺失。
- 即使开放工具，最佳Agent仅解决65%的任务，说明感知增强并不能消除科学表示规则和领域应用错误。
- 某一模型GLM-4.6V在工具辅助后反而下降，表明增加推理资源不保证普遍收益。
- 高置信回答的最高准确率仍仅约30%，自报置信度尚不能充当可靠的自动放行机制。

**证据：**

- [第3.3节 Artifact sourcing and task construction](http://arxiv.org/abs/2608.21357v1)：VIALS includes artifacts that are unpublished real experimental data, artifacts created through procedural generation with scientific software, and artifacts sourced from open-access publications with a CC-BY license. The benchmark emphasizes messy data artifacts that are typical in real- world research. Procedural generation using scientific software was used for select artifact types: phylogenetic trees, plasmid maps, and blots (all of which were expert-verified as realistic).
- [第5.1节 Overall model performance](http://arxiv.org/abs/2608.21357v1)：Figure 4 shows overall accuracy of each vision-language model on VIALS. The top-performing models, GPT-5.6 Sol and Gemini 3.7 Flash, achieve only 26.5% accuracy, with Gemini 3.7 Flash incurring lower costs (Figure A2). The Pass^3 results in Figure A1 show that these models are only able to handle under 17% of VIALS tasks if we require that all 3 rollouts from the model are correct, a basic requirement for trusting a model in critical life sciences research.
- [第5.2节 Failure mode analysis](http://arxiv.org/abs/2608.21357v1)：Most failures occur while models are reading or interpreting the artifact. Across models, 83–93% of errors fall into the first four categories in Table 3. Visual quantification is the most common failure mode for every model, accounting for 28.2–41.8% of errors. Incorrect value or feature selection, overlooked evidence, and representation misinterpretation are also common across models. Quantitative reasoning errors account for 3.8–10.7% of failures, while misapplied scientific principles account for 2.3–6.4%. Fabricated findings are rare, accounting for at most 4.3% of errors.
- [第6节 Conclusion](http://arxiv.org/abs/2608.21357v1)：Our agentic tool-assisted evaluation further decomposes the deficit of current frontier models into two gaps. First, aperception gap: when the same models are given code-execution tools to iteratively crop, zoom, and measure the artifact, accuracy improves by up to 43 points, indicating that much of the required scientific knowledge is present but cannot be applied through the neural network’s direct visual inference (Tong et al., 2024). However, these gains come at10–432× the token cost of direct inference, for interpretations that trained scientists can resolve in minutes. Second, a residual interpretation gap: even with unrestricted iterative inspection and programmatic tool use, the best agent solves only 65% of tasks, with failures concentrated in reading scientific representations and conventions along with proper application of other domain knowledge.

## A股市场观察

市场温度：**偏弱**；上涨 1460 家，下跌 3965 家，中位涨跌幅 -1.22%。

### 规则候选

| 排名 | 代码 | 名称 | 综合分 | 涨跌幅 | 60日 | 入选原因 |
|---:|---|---|---:|---:|---:|---|
| 1 | 601919 | 中远海控 | 90.5 | 2.41% | 27.15% | 中期趋势靠前、估值相对占优、成交活跃 |
| 2 | 000975 | 山金国际 | 83.7 | 2.38% | 30.28% | 中期趋势靠前、成交活跃、量比与换手适中 |
| 3 | 601318 | 中国平安 | 83.2 | 2.94% | 6.13% | 估值相对占优、成交活跃、市值稳定性较高 |
| 4 | 600919 | 江苏银行 | 82.9 | 1.59% | 9.70% | 中期趋势靠前、估值相对占优、成交活跃 |
| 5 | 000001 | 平安银行 | 82.5 | 1.31% | 9.37% | 中期趋势靠前、估值相对占优、成交活跃 |
| 6 | 600036 | 招商银行 | 82.3 | 1.75% | 6.95% | 估值相对占优、成交活跃、量比与换手适中 |
| 7 | 601233 | 桐昆股份 | 82.2 | -0.85% | 29.22% | 中期趋势靠前、估值相对占优、成交活跃 |
| 8 | 600489 | 中金黄金 | 82.0 | 0.29% | 24.12% | 中期趋势靠前、成交活跃、量比与换手适中 |
| 9 | 601229 | 上海银行 | 82.0 | 1.33% | 12.03% | 中期趋势靠前、估值相对占优、成交活跃 |
| 10 | 601077 | 渝农商行 | 81.3 | 3.38% | 7.94% | 中期趋势靠前、估值相对占优、成交活跃 |
| 11 | 601328 | 交通银行 | 80.6 | 1.12% | 10.91% | 中期趋势靠前、估值相对占优、成交活跃 |
| 12 | 601939 | 建设银行 | 80.4 | 1.23% | 9.25% | 估值相对占优、成交活跃、市值稳定性较高 |

### 相对强势行业

- 煤炭行业：2.07%（领涨：上海能源）
- 公路桥梁：2.00%（领涨：皖通高速）
- 开发区：1.87%（领涨：市北高新）
- 酿酒行业：1.65%（领涨：会稽山）
- 金融行业：1.08%（领涨：锦龙股份）
- 农林牧渔：1.07%（领涨：天山生物）
- 建筑建材：0.04%（领涨：北方国际）
- 摩托车：0.03%（领涨：隆鑫通用）

---
本报告仅做公开信息整理与研究观察。科技事件与公司关联不进入规则股票评分，不构成投资建议。
