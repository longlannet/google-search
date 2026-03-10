#!/usr/bin/env python3


def summarize_response_shape(data):
    if not isinstance(data, dict):
        return {'topLevelType': type(data).__name__}

    list_lengths = {}
    for key, value in data.items():
        if isinstance(value, list):
            list_lengths[key] = len(value)

    return {
        'topLevelKeys': sorted(list(data.keys())),
        'listLengths': list_lengths,
        'hasOrganic': bool(data.get('organic')),
        'hasAnswerBox': bool(data.get('answerBox')),
        'hasKnowledgeGraph': bool(data.get('knowledgeGraph')),
        'hasCredits': 'credits' in data,
        'hasSearchParameters': isinstance(data.get('searchParameters'), dict),
    }
