from .media import asset_info


def source_credits(project):
    rows=['Источники изображений и видео', 'Сохраните указанные авторство, ссылки и условия лицензий при публикации ролика.', '']
    seen=set()
    for scene in project.scenes:
        if not scene.asset_id or scene.asset_id in seen:
            continue
        seen.add(scene.asset_id)
        meta=asset_info(scene.asset_id)
        source=meta.get('provenance')
        if source:
            rows.extend([meta['name'], 'Автор: '+source.get('author','Не указан'),
                         'Источник: '+source['source_url'], 'Лицензия: '+source['license'],
                         source.get('license_url',''), 'Изменения: кадрирование, цветокоррекция, анимация и текст в видеомонтаже.', ''])
    if len(rows)==3:
        rows.append('В проекте нет материалов, импортированных поиском Wikimedia Commons, Pexels или Pixabay.')
    return '\n'.join(rows)
