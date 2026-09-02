import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
} from 'typeorm';

@Entity('market_embeddings')
export class MarketEmbedding {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column()
  source: string;

  @Column({ type: 'text' })
  content: string;

  @Column({ type: 'vector', length: 1024 })
  embedding: number[];

  @Column()
  category: string;

  @Column({ type: 'date', nullable: true })
  collectedAt: Date;

  @CreateDateColumn()
  createdAt: Date;
}
