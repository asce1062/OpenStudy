ALTER TABLE public.study_topics
    ADD COLUMN IF NOT EXISTS mastery_state text DEFAULT 'not_started',
    ADD COLUMN IF NOT EXISTS retry_count integer DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS last_attempted_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS last_completed_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS next_review_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS last_confidence integer,
    ADD COLUMN IF NOT EXISTS error_count integer DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS failure_reason text,
    ADD COLUMN IF NOT EXISTS struggle_tags text[],
    ADD COLUMN IF NOT EXISTS retry_priority integer DEFAULT 0 NOT NULL;

ALTER TABLE public.tasks
    ADD COLUMN IF NOT EXISTS mastery_state text DEFAULT 'not_started',
    ADD COLUMN IF NOT EXISTS retry_count integer DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS last_attempted_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS last_completed_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS last_reviewed_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS next_review_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS last_confidence integer,
    ADD COLUMN IF NOT EXISTS error_count integer DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS failure_reason text,
    ADD COLUMN IF NOT EXISTS struggle_tags text[],
    ADD COLUMN IF NOT EXISTS retry_priority integer DEFAULT 0 NOT NULL;

CREATE INDEX IF NOT EXISTS idx_study_topics_mastery_state
    ON public.study_topics (mastery_state);

CREATE INDEX IF NOT EXISTS idx_study_topics_next_review
    ON public.study_topics (next_review_at);

CREATE INDEX IF NOT EXISTS idx_study_topics_retry_priority
    ON public.study_topics (retry_priority);

CREATE INDEX IF NOT EXISTS idx_tasks_mastery_state
    ON public.tasks (mastery_state);

CREATE INDEX IF NOT EXISTS idx_tasks_next_review
    ON public.tasks (next_review_at);

CREATE INDEX IF NOT EXISTS idx_tasks_retry_priority
    ON public.tasks (retry_priority);
