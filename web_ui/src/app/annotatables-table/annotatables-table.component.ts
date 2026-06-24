import { Component, EventEmitter, inject, OnDestroy, OnInit, Output } from '@angular/core';
import { Subscription, take } from 'rxjs';
import { SingleAnnotationService } from '../single-annotation.service';

import { FormsModule } from '@angular/forms';
import { AnnotatableHistory } from '../single-annotation';

@Component({
  selector: 'app-annotatables-table',
  imports: [FormsModule],
  templateUrl: './annotatables-table.component.html',
  styleUrl: './annotatables-table.component.css'
})
export class AnnotatablesTableComponent implements OnInit, OnDestroy {
  public annotatables: AnnotatableHistory[] = [];
  public editingId: number = null;
  public editingNote = '';
  private refreshAnnotatablesSubscription = new Subscription();
  @Output() public emitAnnotatable = new EventEmitter<string>();

  private readonly singleAnnotationService = inject(SingleAnnotationService);

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

  public startEdit(annotatable: AnnotatableHistory): void {
    this.editingId = annotatable.id;
    this.editingNote = annotatable.note;
  }

  public cancelEdit(): void {
    this.editingId = null;
    this.editingNote = '';
  }

  public saveEdit(annotatable: AnnotatableHistory): void {
    this.singleAnnotationService.updateNote(annotatable.name, this.editingNote).subscribe(() => {
      annotatable.note = this.editingNote;
      this.editingId = null;
      this.editingNote = '';
    });
  }

  public ngOnDestroy(): void {
    this.refreshAnnotatablesSubscription.unsubscribe();
  }

  public makeRequest(annotatable: string): void {
    this.emitAnnotatable.emit(annotatable);
  }
}
