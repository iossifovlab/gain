import { CommonModule } from '@angular/common';
import { Component, EventEmitter, OnInit, Output } from '@angular/core';
import { FileContent } from './jobs';
import { JobsService } from './jobs.service';
import { take } from 'rxjs';
import { FormsModule } from '@angular/forms';
import { ColumnSpecifyingComponent } from '../column-specifying/column-specifying.component';
import { UsersService } from '../users.service';
import { SingleAnnotationService } from '../single-annotation.service';

@Component({
  selector: 'app-job-creation',
  imports: [
    CommonModule,
    FormsModule,
    ColumnSpecifyingComponent,
  ],
  templateUrl: './job-creation.component.html',
  styleUrl: './job-creation.component.css'
})
export class JobCreationComponent implements OnInit {
  public file: File = null;
  public fileSeparator: string = null;
  public fileContent: FileContent;
  public requireGenome = false;
  public uploadError = '';
  public selectedGenome = '';
  public genomes : string[] = [];
  @Output() public emitFile = new EventEmitter<File>();
  @Output() public emitFileSeparator = new EventEmitter<string>();
  @Output() public emitGenome = new EventEmitter<string>();
  @Output() public emitUpdatedFileHeader = new EventEmitter<Map<string, string>>();

  public userLimitations: {
      dailyJobs: number;
      filesize: string;
      todayJobsCount: number;
      variantCount: number;
      diskSpace: string;
  } = null;

  public constructor(
    private jobsService: JobsService,
    private usersService: UsersService,
    private singleAnnotationService: SingleAnnotationService,
  ) { }

  public ngOnInit(): void {
    this.usersService.userData.pipe(
    ).subscribe((userData) => {
      this.userLimitations = userData.limitations;
    });

    this.singleAnnotationService.getGenomes().pipe(take(1)).subscribe((genomes) => {
      this.genomes = genomes;
      this.selectedGenome = genomes[0];
      this.emitGenome.emit(this.selectedGenome);
    });
  }

  public getColumns(mappedColumns: Map<string, string>): void {
    this.emitUpdatedFileHeader.emit(mappedColumns);
    if (mappedColumns && (mappedColumns.has('location') || mappedColumns.has('variant'))) {
      this.requireGenome = true;
    } else {
      this.requireGenome = false;
    }
  }

  public submitNewSeparator(newSeparator: string): void {
    this.jobsService.submitSeparator(this.file, newSeparator).pipe(take(1)).subscribe(res => {
      this.fileContent = res;
      this.updateFileSeparator(res.separator);
    });
  }

  public onUpload(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      this.onFileChange(input.files[0]);
    }
  }

  public onDragOver(event: Event): void {
    event.preventDefault();
  }

  public onDropSuccess(event: DragEvent): void {
    event.preventDefault();
    if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length > 0) {
      const file = event.dataTransfer.files[0];
      this.onFileChange(file);
    }
  }

  private isFormatValid(file: File): boolean {
    const validFileExtensions = ['csv', 'vcf', 'tsv', 'txt', 'gz', 'bgz'];
    const fileExtention = file.name.split('.').reverse()[0];
    if (!validFileExtensions.includes(fileExtention)) {
      this.uploadError = 'Unsupported format!';
      return false;
    }
    return true;
  }

  public resetState(): void {
    this.updateFile(null);
    this.uploadError = '';
    this.requireGenome = false;
    this.fileContent = null;
    this.updateFileSeparator('');
  }

  private onFileChange(file: File): void {
    if (!this.isFormatValid(file)) {
      this.file = file;
      this.emitFile.emit(null);
      return;
    }
    this.updateFile(file);
    if (file.name.search('.vcf') === -1) {
      this.createFilePreview();
    }
  }

  private createFilePreview(): void {
    this.jobsService.createFilePreview(this.file).pipe(take(1)).subscribe(res => {
      this.fileContent = res;
      this.updateFileSeparator(res.separator);
    });
  }

  private updateFileSeparator(newSeparator: string): void {
    this.fileSeparator = newSeparator;
    this.emitFileSeparator.emit(this.fileSeparator);
  }

  private updateFile(newFile: File): void {
    this.file = newFile;
    this.emitFile.emit(this.file);
  }

  public onSelectGenome(genome: string): void {
    this.emitGenome.emit(genome);
  }
}
