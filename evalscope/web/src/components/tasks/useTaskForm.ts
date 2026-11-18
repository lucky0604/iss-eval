import { useCallback, useState, type SyntheticEvent } from 'react'
import { useFormErrors } from '@/hooks/useFormErrors'
import { computeFirstInvalid } from '@/domain/form/validation'

interface TaskFormOptions {
  /** Field ids in DOM order; drives submit-time first-invalid focus. */
  domOrder: string[]
  /**
   * Field ids that live inside the collapsible "more parameters" section. When
   * the first invalid field is one of these the section is expanded before focus
   * moves, so the user is never sent to a field they cannot see.
   */
  moreParamsIds?: string[]
  /** Hide errors from an earlier draft after loading or resetting field values. */
  values?: Record<string, string | boolean>
}

export interface TaskForm {
  /** Message key of a field's current error, or `undefined`. */
  errorFor: (id: string) => string | undefined
  /** Clear a field's error, typically as the user edits it. */
  clearError: (id: string) => void
  /** Whether the collapsible section is open. */
  showMore: boolean
  toggleMore: () => void
  /**
   * Run validation now, publish every error and focus the first invalid field.
   * Returns true when the form is valid.
   */
  validateAndPublish: (validate: () => Record<string, string>) => boolean
  /**
   * Refresh errors that are already visible after a dependent edit without
   * introducing new ones: still-invalid fields keep (or update) their error,
   * fixed fields lose it, and fields hidden by a source switch are dropped.
   */
  revalidate: (
    validate: () => Record<string, string>,
    isVisible: (id: string) => boolean,
  ) => void
  /**
   * Build the form's submit handler.
   *
   * @param validate Returns field id -> message key for every invalid field; an
   *   empty object means the form is valid.
   * @param onValid Runs only when validation passed.
   */
  submitHandler: (
    validate: () => Record<string, string>,
    onValid: () => void,
  ) => (event: SyntheticEvent<HTMLFormElement>) => void
}

/**
 * Submit-time error handling shared by the task configuration forms.
 *
 * The Eval and Performance forms describe different fields but behave
 * identically once submitted: collect the errors, publish them for the `Field`
 * primitives to render, then move focus to the first offending field in DOM
 * order — expanding the collapsible section first when the field is hidden
 * inside it. Focus is deferred a frame so a newly expanded section is mounted
 * before it is targeted.
 *
 * The field values themselves stay with the caller: only the error/focus
 * protocol is shared, so neither form has to describe its inputs to this hook.
 */
export function useTaskForm({ domOrder, moreParamsIds = [], values }: TaskFormOptions): TaskForm {
  const { setErrors, errorFor: storedErrorFor, clearError } = useFormErrors()
  const [validatedValues, setValidatedValues] = useState<Record<string, string | boolean>>({})
  const [showMore, setShowMore] = useState(false)
  const errorFor = (id: string) => {
    if (values && values[id] !== validatedValues[id]) return undefined
    return storedErrorFor(id)
  }

  const toggleMore = useCallback(() => setShowMore((open) => !open), [])

  const validateAndPublish = useCallback(
    (validate: () => Record<string, string>): boolean => {
      const errors = validate()
      setValidatedValues(values ? { ...values } : {})

      if (Object.keys(errors).length > 0) {
        setErrors(errors)
        const firstInvalid = computeFirstInvalid(domOrder, Object.keys(errors))
        if (firstInvalid) {
          if (moreParamsIds.includes(firstInvalid)) setShowMore(true)
          // Defer focus so a newly-expanded section is mounted first.
          requestAnimationFrame(() => document.getElementById(firstInvalid)?.focus())
        }
        return false
      }

      setErrors({})
      return true
    },
    [setErrors, domOrder, moreParamsIds, values],
  )

  const revalidate = useCallback(
    (validate: () => Record<string, string>, isVisible: (fieldId: string) => boolean) => {
      const errors = validate()
      setValidatedValues((previous) => {
        if (!values || Object.keys(values).every((key) => values[key] === previous[key])) return previous
        return { ...values }
      })
      setErrors((previous) => {
        let changed = false
        const next = { ...previous }
        for (const fieldId of Object.keys(previous)) {
          if (!isVisible(fieldId)) {
            delete next[fieldId]
            changed = true
          } else if (errors[fieldId]) {
            if (next[fieldId] !== errors[fieldId]) {
              next[fieldId] = errors[fieldId]
              changed = true
            }
          } else {
            delete next[fieldId]
            changed = true
          }
        }
        return changed ? next : previous
      })
    },
    [setErrors, values],
  )

  const submitHandler = useCallback(
    (validate: () => Record<string, string>, onValid: () => void) => (event: SyntheticEvent<HTMLFormElement>) => {
      event.preventDefault()
      if (validateAndPublish(validate)) onValid()
    },
    [validateAndPublish],
  )

  return { errorFor, clearError, showMore, toggleMore, validateAndPublish, revalidate, submitHandler }
}
