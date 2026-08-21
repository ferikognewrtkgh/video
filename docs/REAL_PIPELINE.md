# 最短真实媒体闭环接入

这条链路只把经过真实 Provider 或人工上传、成功解码并落盘的文件视为真实产物。默认 Mock 不能进入最终合成。

```text
ShotSpec
  → 2–4 个真实关键帧任务
  → 人工选择并锁定一个关键帧
  → 真实 I2V 任务
  → Provider 产物下载、格式校验、SHA-256、OpenCV 硬门禁
  → 真实 TTS 音轨
  → Shot 时间轴生成 SRT
  → FFmpeg 统一分辨率/帧率并拼接
  → ffprobe 验证 video + audio + subtitle 三轨
  → 可下载 MP4 与 Delivery Manifest
```

## 需要提供的接入信息

要在当前环境实际生成第一条成片，需要提供以下内容：

1. 图片生成：选择 ComfyUI 图片工作流，或提供异步图片 API 的 Base URL、API Key 和模型名。
2. 视频生成：选择 ComfyUI I2V 工作流，或提供异步视频 API 的 Base URL、API Key 和模型名。
3. 配音：提供异步 TTS API 的 Base URL、API Key、模型名和默认声音。
4. 媒体互通：云端 I2V 必须能访问锁定关键帧，因此需要一个外部可访问的 HTTPS Base URL 和产物签名密钥。
5. 产物下载：提供 Provider 输出 CDN/S3 的域名白名单。
6. 本机工具：安装 `ffmpeg` 与 `ffprobe`，或提供两个可执行文件的绝对路径。

密钥只放在后端环境或 Secret Manager，不进入前端、Git 或日志。

## 配置方式 A：ComfyUI 图片与视频

```env
MANGAFLOW_IMAGE_PROVIDER=comfyui
MANGAFLOW_PROVIDER=comfyui
COMFYUI_BASE_URL=http://127.0.0.1:8188
COMFYUI_IMAGE_WORKFLOW_PATH=./workflows/keyframe.json
COMFYUI_WORKFLOW_PATH=./workflows/i2v.json
IMAGE_MODEL=your-keyframe-workflow-version
```

I2V 工作流应在标准 `LoadImage` 节点的文件名输入中使用 `{{reference_uri}}`。服务会先把已批准关键帧上传到 ComfyUI 的 `/upload/image`，再把返回的文件名注入工作流，不要求 ComfyUI 主动访问公网图片。也可通过任务参数 `workflow_inputs` 覆盖明确的节点输入；不存在的节点会被拒绝，避免静默生成错误工作流。

## 配置方式 B：云端异步任务 API

```env
MANGAFLOW_IMAGE_PROVIDER=cloud-image
IMAGE_PROVIDER_BASE_URL=https://image-provider.example/v1
IMAGE_PROVIDER_API_KEY=...
IMAGE_MODEL=...

MANGAFLOW_PROVIDER=cloud-video
CLOUD_VIDEO_BASE_URL=https://video-provider.example/v1
CLOUD_VIDEO_API_KEY=...

MANGAFLOW_TTS_PROVIDER=cloud-tts
TTS_PROVIDER_BASE_URL=https://tts-provider.example/v1
TTS_PROVIDER_API_KEY=...
TTS_MODEL=...
TTS_DEFAULT_VOICE=...

MANGAFLOW_PUBLIC_MEDIA_BASE_URL=https://studio.example.com
MANGAFLOW_ARTIFACT_SIGNING_SECRET=...
MANGAFLOW_MEDIA_DOWNLOAD_HOSTS=image-cdn.example.com,video-cdn.example.com,audio-cdn.example.com

FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe
```

云端适配器当前使用以下合同：

| 方法 | 路径 | 最小响应 |
| --- | --- | --- |
| `POST` | `/{image|video|audio}/tasks` | `{"id":"...","status":"queued"}` |
| `GET` | `/{kind}/tasks/{id}` | `{"id":"...","status":"succeeded","output_uri":"https://...","cost":0.1,"elapsed_sec":12}` |
| `GET` | `/{kind}/tasks/by-idempotency/{key}` | 原任务或 `404` |
| `POST` | `/{kind}/tasks/{id}/cancel` | 2xx |
| `GET` | `/health` | 2xx |

提交请求包含 `Idempotency-Key`。视频请求在 I2V 路线中包含签名后的 `reference_uri`；Provider 输出只有在域名白名单内才会被下载。

## 验收顺序

1. `GET /api/production-readiness?probe=true`：所有 Provider 健康且 FFmpeg/ffprobe 可用。
2. `POST /api/projects/{project}/shots/{shot}/keyframes/generate`：提交 2–4 个候选。
3. 对每个 Run 调用 `POST /api/projects/{project}/runs/{run}/tick`，直到产物落盘。
4. `POST /api/projects/{project}/shots/{shot}/approve-keyframe`，请求体包含 `artifact_id`。
5. `POST /api/projects/{project}/shots/{shot}/generate`，然后轮询 Run。
6. `POST /api/projects/{project}/shots/{shot}/speech/generate`，然后轮询 Run。
7. `POST /api/projects/{project}/subtitles/build`。
8. 所有镜头完成后调用 `POST /api/projects/{project}/assemble`。
9. 从响应的 `download_url` 下载 MP4，并核对 SHA-256、OpenCV 检查和 ffprobe 三轨结果。

也可以通过 `PUT /api/projects/{project}/shots/{shot}/artifacts/{keyframe|video|audio}` 上传真实媒体，以便先验证审核和合成链路。上传使用原始二进制 Body，并通过 `X-Filename` 和可选 `X-Content-SHA256` 传递文件信息。

## 失败关闭原则

- Provider 未配置时返回 `503`，不会自动改用 Mock。
- I2V 没有已批准且 Provider 可访问的关键帧时返回 `409`。
- Provider 输出域名不在白名单时拒绝下载。
- 文件无法解码、哈希不一致或超出大小限制时拒绝落盘。
- 任一镜头缺少视频、音频或已批准关键帧时拒绝合成。
- 最终 MP4 缺少视频、音频、字幕任一轨道，或未通过媒体硬门禁时，删除失败输出且不生成交付记录。
