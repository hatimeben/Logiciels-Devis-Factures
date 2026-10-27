def format_document_number(settings, year, sequence):
    parts = [settings.prefix]
    if settings.include_year:
        parts.append(str(year))
    parts.append(str(sequence).zfill(settings.sequence_width))
    return settings.separator.join(parts)
