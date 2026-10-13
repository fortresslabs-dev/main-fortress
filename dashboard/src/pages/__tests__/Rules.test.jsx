import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Rules from '../Rules'

vi.mock('../../services/api')

describe('<Rules />', () => {
  it('renders the rules header', async () => {
    renderWithRouter(<Rules />)
    expect(await screen.findByRole('heading', { name: /Rules/i, level: 1 })).toBeInTheDocument()
  })
})
