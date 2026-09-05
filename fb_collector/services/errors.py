class UserVisibleError(Exception):
    user_message = "抓取失败，请查看运行记录"

    def __init__(self, message=None, technical=None):
        super().__init__(technical or message or self.user_message)
        if message:
            self.user_message = message
        self.technical = technical or str(self)


class InvalidLinkError(UserVisibleError):
    user_message = "链接无效"


class LoginRequiredError(UserVisibleError):
    user_message = "需要登录 Facebook"


class PermissionDeniedError(UserVisibleError):
    user_message = "没有权限查看该贴文"


class VerificationRequiredError(UserVisibleError):
    user_message = "遇到验证码或安全验证"


class BrowserStartError(UserVisibleError):
    user_message = "浏览器启动失败"


class MediaError(UserVisibleError):
    user_message = "媒体读取失败"


class UploadError(UserVisibleError):
    user_message = "图片上传失败"


class OcrError(UserVisibleError):
    user_message = "OCR失败"


class AudioError(UserVisibleError):
    user_message = "音频识别失败"


class TranslationError(UserVisibleError):
    user_message = "翻译失败"


class SheetWriteError(UserVisibleError):
    user_message = "写入表格失败"


class RateLimitError(UserVisibleError):
    user_message = "今日 Facebook 接口请求次数已达上限"
