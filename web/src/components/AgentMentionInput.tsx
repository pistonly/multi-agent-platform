import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type CompositionEvent,
  type KeyboardEvent,
} from "react";
import { useAgents } from "../hooks/useAgents";
import type { Agent } from "../api/types";
import {
  detectMentionTrigger,
  filterMentionCandidates,
  reduceMentionKey,
  replaceMention,
  wrapActiveIndex,
  type MentionContext,
  type MentionKey,
} from "../utils/mentionInput";

interface MentionInputShellProps {
  open: boolean;
  candidates: Agent[];
  activeIndex: number;
  onSelect: (agent: Agent) => void;
  onHover: (index: number) => void;
  /** Optional test id override for the popover list. */
  listTestId?: string;
  /** Optional id base, used to wire aria attributes. */
  idPrefix: string;
}

function MentionList({
  open,
  candidates,
  activeIndex,
  onSelect,
  onHover,
  listTestId,
  idPrefix,
}: MentionInputShellProps) {
  if (!open) return null;
  if (candidates.length === 0) {
    return (
      <ul
        role="listbox"
        id={`${idPrefix}-listbox`}
        data-testid={listTestId ?? "mention-popover"}
        className="absolute z-20 mt-1 max-h-56 w-64 overflow-auto rounded-md border border-surface-border bg-surface-raised p-1 text-sm shadow-lg"
      >
        <li className="px-3 py-2 text-xs text-slate-500">无匹配 agent</li>
      </ul>
    );
  }
  return (
    <ul
      role="listbox"
      id={`${idPrefix}-listbox`}
      data-testid={listTestId ?? "mention-popover"}
      className="absolute z-20 mt-1 max-h-56 w-64 overflow-auto rounded-md border border-surface-border bg-surface-raised p-1 text-sm shadow-lg"
    >
      {candidates.map((agent, idx) => {
        const active = idx === activeIndex;
        return (
          <li
            key={agent.id}
            role="option"
            aria-selected={active}
            data-testid={`mention-option-${agent.id}`}
            onMouseDown={(e) => {
              // mousedown (not click) so the input keeps focus and the click
              // happens before the textarea blurs and closes the popover.
              e.preventDefault();
              onSelect(agent);
            }}
            onMouseEnter={() => onHover(idx)}
            className={`flex cursor-pointer flex-col rounded px-2 py-1 ${
              active ? "bg-accent/20 text-white" : "text-slate-200 hover:bg-surface"
            }`}
          >
            <span className="truncate font-medium">{agent.name}</span>
            <span className="truncate text-[11px] text-slate-500">
              {[agent.role, agent.project_key].filter(Boolean).join(" · ") || agent.id.slice(0, 8)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

interface UseMentionControllerOptions {
  /** The element ref whose `value` / caret position we read. */
  inputRef: React.RefObject<HTMLInputElement | HTMLTextAreaElement>;
  /** Agent list (already filtered / cached by useAgents). */
  agents: Agent[];
  /** Notified whenever the user picks a candidate so we can update state. */
  onCommit: (nextValue: string, nextCaret: number) => void;
  /** Optional cap on how many candidates to show. */
  maxCandidates?: number;
}

interface MentionController {
  open: boolean;
  ctx: MentionContext | null;
  candidates: Agent[];
  activeIndex: number;
  setActiveIndex: (n: number) => void;
  recompute: () => void;
  close: () => void;
  handleKeyDown: (e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => boolean;
  handleSelect: (agent: Agent) => void;
  /** IME composition handlers the host input must wire up. */
  compositionHandlers: {
    onCompositionStart: (e: CompositionEvent) => void;
    onCompositionEnd: (e: CompositionEvent) => void;
  };
}

/**
 * Encapsulates the @-mention popover controller so the same logic powers
 * both <input> (single-line reply) and <textarea> (topic / general comment).
 */
function useMentionController({
  inputRef,
  agents,
  onCommit,
  maxCandidates,
}: UseMentionControllerOptions): MentionController {
  const [ctx, setCtx] = useState<MentionContext | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  // Suppress popover recomputation / key handling while a CJK IME is in the
  // composition phase (see plan risk: 中文输入法组合输入期间不应触发弹层).
  const composingRef = useRef(false);

  const recompute = useCallback(() => {
    if (composingRef.current) return;
    const el = inputRef.current;
    if (!el) {
      setCtx(null);
      return;
    }
    const caret = el.selectionStart ?? el.value.length;
    const next = detectMentionTrigger(el.value, caret);
    setCtx(next);
    setActiveIndex(0);
  }, [inputRef]);

  const close = useCallback(() => setCtx(null), []);

  const candidates = useMemo(() => {
    if (!ctx) return [];
    return filterMentionCandidates(agents, ctx.query, maxCandidates);
  }, [agents, ctx, maxCandidates]);

  const handleSelect = useCallback(
    (agent: Agent) => {
      const el = inputRef.current;
      if (!el || !ctx) return;
      const { value, caret } = replaceMention(el.value, ctx, agent.name);
      onCommit(value, caret);
      setCtx(null);
    },
    [ctx, inputRef, onCommit],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>): boolean => {
      // While the IME is composing we leave the key alone so the user can
      // type the CJK candidate without the popover intercepting Enter
      // (which would otherwise commit an agent name mid-composition).
      if (composingRef.current) return false;
      const result = reduceMentionKey(e.key as MentionKey, {
        popoverOpen: !!ctx,
        candidateCount: candidates.length,
        activeIndex,
      });
      if (!result.handled) return false;
      e.preventDefault();
      switch (result.action) {
        case "next":
          setActiveIndex((idx) => wrapActiveIndex(idx + 1, candidates.length));
          return true;
        case "prev":
          setActiveIndex((idx) =>
            wrapActiveIndex(idx - 1, candidates.length),
          );
          return true;
        case "commit":
          handleSelect(candidates[result.index]);
          return true;
        case "close":
          setCtx(null);
          return true;
      }
    },
    [activeIndex, candidates, ctx, handleSelect],
  );

  const onCompositionStart = useCallback((_e: CompositionEvent) => {
    composingRef.current = true;
  }, []);
  const onCompositionEnd = useCallback(
    (_e: CompositionEvent) => {
      composingRef.current = false;
      // Now that the composed character is committed we re-evaluate the
      // trigger so the popover can react to the new text.
      recompute();
    },
    [recompute],
  );

  return {
    open: !!ctx,
    ctx,
    candidates,
    activeIndex,
    setActiveIndex,
    recompute,
    close,
    handleKeyDown,
    handleSelect,
    // IME wiring is consumed by the host component, not the controller.
    compositionHandlers: {
      onCompositionStart,
      onCompositionEnd,
    },
  };
}

export interface AgentMentionTextareaProps
  extends Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, "onChange"> {
  value: string;
  onValueChange: (next: string) => void;
  /** Cap on the number of candidates shown (default 50). */
  maxCandidates?: number;
}

/**
 * <textarea> wrapper that pops up an agent-autocomplete list whenever the
 * user types `@`. Keyboard: ↑/↓ to move, Enter / Tab to pick, Esc to close.
 */
export function AgentMentionTextarea({
  value,
  onValueChange,
  maxCandidates,
  className,
  onKeyDown,
  ...rest
}: AgentMentionTextareaProps) {
  const { agents } = useAgents();
  const ref = useRef<HTMLTextAreaElement>(null);
  const idPrefix = useId();
  const [internalValue, setInternalValue] = useState(value);

  // Keep local mirror in sync with parent-owned value (controlled input).
  useEffect(() => {
    setInternalValue(value);
  }, [value]);

  const commit = useCallback(
    (nextValue: string) => {
      setInternalValue(nextValue);
      onValueChange(nextValue);
    },
    [onValueChange],
  );

  const { open, candidates, activeIndex, setActiveIndex, recompute, handleKeyDown, handleSelect } =
    useMentionController({
      inputRef: ref,
      agents,
      onCommit: (nextValue, nextCaret) => {
        commit(nextValue);
        // Restore the caret after the parent has re-rendered with the new
        // value. requestAnimationFrame waits for React to flush.
        requestAnimationFrame(() => {
          const el = ref.current;
          if (!el) return;
          try {
            el.setSelectionRange(nextCaret, nextCaret);
          } catch {
            // Some input types reject setSelectionRange — safe to ignore.
          }
          recompute();
        });
      },
      maxCandidates,
    });

  return (
    <div className="relative">
      <textarea
        ref={ref}
        {...rest}
        value={internalValue}
        className={className}
        onChange={(e: ChangeEvent<HTMLTextAreaElement>) => {
          commit(e.target.value);
          recompute();
        }}
        onKeyDown={(e) => {
          const handled = handleKeyDown(e);
          if (!handled) onKeyDown?.(e);
        }}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? `${idPrefix}-listbox` : undefined}
      />
      <MentionList
        open={open}
        candidates={candidates}
        activeIndex={activeIndex}
        onSelect={handleSelect}
        onHover={setActiveIndex}
        idPrefix={idPrefix}
      />
    </div>
  );
}

export interface AgentMentionInputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange"> {
  value: string;
  onValueChange: (next: string) => void;
  maxCandidates?: number;
}

/**
 * Single-line <input> variant of the @-mention popover. Used by the
 * inline reply box on each comment.
 */
export function AgentMentionInput({
  value,
  onValueChange,
  maxCandidates,
  className,
  onKeyDown,
  ...rest
}: AgentMentionInputProps) {
  const { agents } = useAgents();
  const ref = useRef<HTMLInputElement>(null);
  const idPrefix = useId();
  const [internalValue, setInternalValue] = useState(value);

  useEffect(() => {
    setInternalValue(value);
  }, [value]);

  const commit = useCallback(
    (nextValue: string) => {
      setInternalValue(nextValue);
      onValueChange(nextValue);
    },
    [onValueChange],
  );

  const { open, candidates, activeIndex, setActiveIndex, recompute, handleKeyDown, handleSelect } =
    useMentionController({
      inputRef: ref,
      agents,
      onCommit: (nextValue, nextCaret) => {
        commit(nextValue);
        requestAnimationFrame(() => {
          const el = ref.current;
          if (!el) return;
          try {
            el.setSelectionRange(nextCaret, nextCaret);
          } catch {
            /* ignore */
          }
          recompute();
        });
      },
      maxCandidates,
    });

  return (
    <div className="relative">
      <input
        ref={ref}
        {...rest}
        value={internalValue}
        className={className}
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          commit(e.target.value);
          recompute();
        }}
        onKeyDown={(e) => {
          const handled = handleKeyDown(e);
          if (!handled) onKeyDown?.(e);
        }}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? `${idPrefix}-listbox` : undefined}
      />
      <MentionList
        open={open}
        candidates={candidates}
        activeIndex={activeIndex}
        onSelect={handleSelect}
        onHover={setActiveIndex}
        idPrefix={idPrefix}
      />
    </div>
  );
}
