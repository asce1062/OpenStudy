CREATE OR REPLACE FUNCTION public.log_table_change() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
  payload jsonb;
  row_id text;
  course_code text;
  event_course_code text;
  kind text;
BEGIN
  -- Extract id (uuid or composite key) best-effort.
  IF TG_OP = 'DELETE' THEN
    BEGIN row_id := (row_to_json(OLD)::jsonb->>'id'); EXCEPTION WHEN OTHERS THEN row_id := NULL; END;
    BEGIN course_code := (row_to_json(OLD)::jsonb->>'course_code'); EXCEPTION WHEN OTHERS THEN course_code := NULL; END;
    IF course_code IS NULL AND TG_TABLE_NAME = 'courses' THEN
      BEGIN course_code := (row_to_json(OLD)::jsonb->>'code'); EXCEPTION WHEN OTHERS THEN course_code := NULL; END;
    END IF;
    payload := jsonb_build_object(
      'table', TG_TABLE_NAME,
      'op', TG_OP,
      'id', row_id,
      'course_code', course_code,
      'before', to_jsonb(OLD)
    );
  ELSE
    BEGIN row_id := (row_to_json(NEW)::jsonb->>'id'); EXCEPTION WHEN OTHERS THEN row_id := NULL; END;
    BEGIN course_code := (row_to_json(NEW)::jsonb->>'course_code'); EXCEPTION WHEN OTHERS THEN course_code := NULL; END;
    IF course_code IS NULL AND TG_TABLE_NAME = 'courses' THEN
      BEGIN course_code := (row_to_json(NEW)::jsonb->>'code'); EXCEPTION WHEN OTHERS THEN course_code := NULL; END;
    END IF;
    IF TG_OP = 'UPDATE' THEN
      payload := jsonb_build_object(
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'id', row_id,
        'course_code', course_code,
        'before', to_jsonb(OLD),
        'after', to_jsonb(NEW)
      );
    ELSE
      payload := jsonb_build_object(
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'id', row_id,
        'course_code', course_code,
        'after', to_jsonb(NEW)
      );
    END IF;
  END IF;

  kind := 'db:' || lower(TG_OP) || ':' || TG_TABLE_NAME;

  -- Keep audit payloads for deleted courses/dependents, but do not make audit
  -- insertion depend on a course row that is currently being removed.
  IF course_code IS NOT NULL AND EXISTS (SELECT 1 FROM public.courses WHERE code = course_code) THEN
    event_course_code := course_code;
  ELSE
    event_course_code := NULL;
  END IF;

  INSERT INTO public.events (kind, course_code, payload)
  VALUES (kind, event_course_code, payload);

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;
