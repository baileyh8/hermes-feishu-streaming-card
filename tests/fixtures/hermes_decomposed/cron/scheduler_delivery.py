def _deliver_result(job, content, adapters=None, loop=None):
    delivery_content = content
    media_files, cleaned_delivery_content = BasePlatformAdapter.extract_media(delivery_content)
    media_files = BasePlatformAdapter.filter_media_delivery_paths(media_files)
    return cleaned_delivery_content, media_files
