import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  OnDestroy,
  Output,
  effect,
  inject,
  viewChild,
} from '@angular/core';
import * as React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { ToastService } from '../../core/toast.service';
import { MeetingService } from './meeting.service';
import { LiveKitRoomReact, type AgentState } from './livekit-room.react';

/**
 * MeetingRoomComponent — the live in-call view.
 *
 * Renders the EXACT same prebuilt LiveKit UI as the Next.js web-app
 * (@livekit/components-react <VideoConference>) by mounting a small React island
 * (LiveKitRoomReact) into an Angular host element via ReactDOM.createRoot. There
 * is no @livekit/components-angular, so this is how Angular gets the real
 * component — not a custom re-creation.
 *
 * Angular owns the session (MeetingService.session()) + the Add-AI action; the
 * React island owns only the room UI and bubbles onAddAi / onLeave back here.
 */
@Component({
  selector: 'va-meeting-room',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div #host class="lk-host"></div>`,
  styles: [`
    :host { display: block; width: 100%; height: 100%; }
    /* Fill the host (the route/page makes it full-screen). */
    .lk-host { width: 100%; height: 100%; min-height: 480px; overflow: hidden; }
  `],
})
export class MeetingRoomComponent implements OnDestroy {
  private meeting = inject(MeetingService);
  private toast = inject(ToastService);

  /** Emitted when the user leaves so the parent can close the room view. */
  @Output() left = new EventEmitter<void>();

  private host = viewChild.required<ElementRef<HTMLDivElement>>('host');
  private root: Root | null = null;
  private agentState: AgentState = 'idle';

  constructor() {
    // Re-render the React island whenever the session or agent state changes.
    effect(() => {
      const session = this.meeting.session();
      // mirror Angular's agentAdded → React button state.
      if (this.meeting.agentAdded() && this.agentState !== 'adding') this.agentState = 'added';
      this.render(session);
    });
  }

  private render(session: { room: string; token: string; serverUrl: string } | null): void {
    const el = this.host().nativeElement;
    if (!session) {
      this.unmount();
      return;
    }
    if (!this.root) this.root = createRoot(el);
    this.root.render(
      React.createElement(LiveKitRoomReact, {
        serverUrl: session.serverUrl,
        token: session.token,
        roomLabel: session.room,
        agentState: this.agentState,
        onAddAi: () => void this.addAi(),
        onLeave: () => void this.onLeave(),
      }),
    );
  }

  private async addAi(): Promise<void> {
    if (this.agentState !== 'idle') return;
    this.agentState = 'adding';
    this.render(this.meeting.session());
    try {
      await this.meeting.addAgent(this.meeting.roomName(), 'panel');
      this.agentState = 'added';
      this.toast.success('AI assistant is joining the meeting.');
    } catch (e) {
      this.agentState = 'idle';
      this.toast.warning(e instanceof Error ? e.message : 'Could not add the AI assistant.');
    }
    this.render(this.meeting.session());
  }

  private async onLeave(): Promise<void> {
    await this.meeting.leave();
    this.left.emit();
  }

  private unmount(): void {
    if (this.root) {
      this.root.unmount();
      this.root = null;
    }
  }

  ngOnDestroy(): void {
    this.unmount();
  }
}
