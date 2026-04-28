import { Component, EventEmitter, OnDestroy, OnInit, Output } from '@angular/core';
import { Subscription, take } from 'rxjs';
import { SingleAnnotationService } from '../single-annotation.service';
import { CommonModule } from '@angular/common';
import { AnnotatableHistory } from '../single-annotation';

@Component({
  selector: 'app-annotatables-table',
  imports: [CommonModule],
  templateUrl: './annotatables-table.component.html',
  styleUrl: './annotatables-table.component.css'
})
export class AnnotatablesTableComponent implements OnInit, OnDestroy {
  public annotatables: AnnotatableHistory[] = [];
  private refreshAnnotatablesSubscription = new Subscription();
  @Output() public emitAnnotatable = new EventEmitter<string>();

  public constructor(private singleAnnotationService: SingleAnnotationService) {}

  public ngOnInit(): void {
    this.getAnnotatables();
    this.refreshTable();
  }

  public refreshTable(): void {
    this.refreshAnnotatablesSubscription.unsubscribe();
    this.refreshAnnotatablesSubscription = this.singleAnnotationService.getAnnotatablesHistory().pipe(
    ).subscribe(history => {
      this.annotatables = history;
    });
  }

  private getAnnotatables(): void {
    this.singleAnnotationService.getAnnotatablesHistory().pipe(take(1)).subscribe(history => {
      this.annotatables = history;
    });
  }

  public onDelete(annotatableId: number): void {
    this.singleAnnotationService.deleteAnnotatable(annotatableId).subscribe(() => this.getAnnotatables());
  }

  public ngOnDestroy(): void {
    this.refreshAnnotatablesSubscription.unsubscribe();
  }

  public makeRequest(annotatable: string): void {
    this.emitAnnotatable.emit(annotatable);
  }
}
