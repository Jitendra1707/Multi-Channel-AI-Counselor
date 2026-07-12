import { Directive, ElementRef, Input, OnInit, inject } from '@angular/core';

/** Adds an `in-view` class when the element scrolls into view (reduced-motion aware). */
@Directive({ selector: '[vaReveal]', standalone: true })
export class RevealDirective implements OnInit {
  @Input() vaReveal: number | string = 0; // delay ms
  private el = inject(ElementRef<HTMLElement>);

  ngOnInit() {
    const node = this.el.nativeElement;
    node.classList.add('reveal');
    const delay = Number(this.vaReveal) || 0;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !('IntersectionObserver' in window)) { node.classList.add('in-view'); return; }
    if (delay) node.style.transitionDelay = delay + 'ms';
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { node.classList.add('in-view'); io.unobserve(node); }
      }
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
    io.observe(node);
  }
}
