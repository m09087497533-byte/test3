"""Optional multilingual ONNX text similarity, evaluated locally on the CPU.

Only source descriptions are compared. This is not image recognition and does
not establish that a file depicts a named person, place or historical document.
"""
from functools import lru_cache
import importlib.util
import threading

from .media import DATA

MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
_model = None
_lock = threading.RLock()
_message = 'Нажмите «Подготовить модель»: первый запуск скачивает около 220 МБ.'


def status():
    return dict(available=importlib.util.find_spec('fastembed') is not None,
                ready=_model is not None, message=_message)


def prepare(*, local_files_only=False):
    global _model, _message
    with _lock:
        if _model is not None:
            return status()
        try:
            from fastembed import TextEmbedding
            _message = 'Загружаем смысловую модель…'
            _model = TextEmbedding(model_name=MODEL, cache_dir=str(DATA / 'models'), threads=2,
                                   local_files_only=local_files_only)
            _message = 'Модель готова. Оценка описаний выполняется на этом компьютере.'
        except Exception:
            _model = None
            _message = 'Модель недоступна. Проверьте установку requirements-ai.txt и доступ к huggingface.co. Подбор по ключевым словам остаётся доступен.'
        return status()


def restore():
    if any((DATA / 'models').rglob('*.onnx')) and status()['available']:
        prepare(local_files_only=True)


@lru_cache(maxsize=2048)
def _embedding(text):
    return next(_model.embed([text]))


def similarity(left, right):
    global _model, _message
    if _model is None or not left.strip() or not right.strip():
        return None
    with _lock:
        try:
            a, b = _embedding(left[:2000]), _embedding(right[:2000])
            denominator = float((a @ a) ** .5 * (b @ b) ** .5)
            return float(a @ b) / denominator if denominator else 0.
        except Exception:
            _model = None
            _embedding.cache_clear()
            _message = 'Смысловая модель остановилась. Повторите подготовку модели. Доступен подбор по ключевым словам.'
            return None

