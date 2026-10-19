"""On-device translation and image/text similarity; no inference API calls."""
import os
import re
from pathlib import Path

CLIP_MODEL = 'openai/clip-vit-base-patch32'
TRANSLATION_MODEL = 'Helsinki-NLP/opus-mt-ru-en'
CACHE = str(Path(os.getenv('LOCAL_MODELS_DIR', str(Path.home()/'.cache'/'ai_footage_models'))).expanduser())
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
os.environ.setdefault('TOKENIZERS_PARALLELISM','false')


def load_pretrained(cls, name, **kwargs):
    # Once downloaded, cached models load without contacting a server.
    try:
        return cls.from_pretrained(name, cache_dir=CACHE, local_files_only=True, **kwargs)
    except (OSError, TypeError, ValueError, AttributeError, AssertionError):
        # Transformers may raise NoneType errors for an incomplete local cache.
        # Retry the normal loader; any error there is reported to the user.
        return cls.from_pretrained(name, cache_dir=CACHE, **kwargs)


class LocalModels:
    def __init__(self, log=lambda message:None):
        self.log=log
        self.clip=None
        self.translator=None
        self.tokenizer=None
        self.processor=None

    def load_clip(self):
        if self.clip is not None:
            return
        self.log('Загружаю локальную модель сравнения кадров; при первом запуске скачиваются веса...')
        import torch
        from transformers import CLIPModel, CLIPProcessor
        torch.set_num_threads(min(4,os.cpu_count() or 2))
        self.processor=load_pretrained(CLIPProcessor,CLIP_MODEL,use_fast=False)
        self.clip=load_pretrained(CLIPModel,CLIP_MODEL).to('cpu').eval()

    def translate(self,text):
        if not re.search(r'[А-Яа-яЁё]',text):
            return text
        import torch
        from transformers import MarianMTModel, MarianTokenizer
        torch.set_num_threads(min(4,os.cpu_count() or 2))
        if self.translator is None:
            self.log('Загружаю локальный переводчик RU→EN; при первом запуске скачиваются веса...')
            self.tokenizer=load_pretrained(MarianTokenizer,TRANSLATION_MODEL)
            self.translator=load_pretrained(MarianMTModel,TRANSLATION_MODEL).to('cpu').eval()
        chunks=[]
        # Bound each piece well below the model's 512 token input length.
        words=text.split()
        for start in range(0,len(words),55):
            piece=' '.join(words[start:start+55])
            inputs=self.tokenizer(piece,return_tensors='pt',truncation=True,max_length=480)
            with torch.inference_mode():
                output=self.translator.generate(**inputs,max_new_tokens=160,num_beams=2)
            chunks.append(self.tokenizer.decode(output[0],skip_special_tokens=True))
        return ' '.join(chunks)

    def text_vectors(self,texts):
        import torch
        import numpy as np
        self.load_clip()
        inputs=self.processor(text=texts,return_tensors='pt',padding=True,truncation=True,max_length=77)
        with torch.inference_mode():
            vectors=self.clip.get_text_features(**inputs)
            vectors=vectors/vectors.norm(dim=-1,keepdim=True).clamp_min(1e-12)
        return np.asarray(vectors.cpu(),dtype='float32')

    def image_vectors(self,images):
        import torch
        import numpy as np
        self.load_clip()
        inputs=self.processor(images=images,return_tensors='pt')
        with torch.inference_mode():
            vectors=self.clip.get_image_features(**inputs)
            vectors=vectors/vectors.norm(dim=-1,keepdim=True).clamp_min(1e-12)
        return np.asarray(vectors.cpu(),dtype='float32')
